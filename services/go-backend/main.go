package main

// main.go — Trinity Guard Go backend (Phase 2 refactor).
//
// Pipeline: HTTP request -> NER (HTTP) -> ZK compliance proof (gRPC, only for
// suspicious transactions) -> LLM SAR narrative (HTTP) -> PostgreSQL store ->
// Redis pub/sub fan-out. Firebase has been removed in favor of Redis pub/sub.
//
// See project.md Phase 2 and contracts/proto/trinity.proto for the contracts.

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	_ "github.com/lib/pq" // PostgreSQL driver
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
)

var (
	transactionsProcessedTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "transactions_processed_total",
		Help: "Total number of transactions processed by the Go backend.",
	}, []string{"status"})

	transactionsFlaggedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "transactions_flagged_total",
		Help: "Total number of transactions flagged as suspicious.",
	})

	sarsGeneratedTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "sars_generated_total",
		Help: "Total number of Suspicious Activity Reports generated.",
	})

	transactionProcessingDuration = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "transaction_processing_duration_seconds",
		Help:    "Time spent processing a single transaction through the full pipeline.",
		Buckets: prometheus.DefBuckets,
	})
)

// TransactionService integrates NER, ZK, LLM, PostgreSQL and Redis pub/sub.
type TransactionService struct {
	db          *sql.DB
	rdb         *redis.Client
	nerEndpoint string
	llmEndpoint string
	zk          *ZKClient
}

// MarbleTransaction represents the transaction structure accepted by the API.
type MarbleTransaction struct {
	ID          string    `json:"id"`
	Amount      float64   `json:"amount"`
	Currency    string    `json:"currency"`
	Description string    `json:"description"`
	Sender      string    `json:"sender"`
	Receiver    string    `json:"receiver"`
	Timestamp   time.Time `json:"timestamp"`
	Status      string    `json:"status"`
	Category    string    `json:"category"`
}

// NERResponse is the response from the Python NER service (/detect).
type NERResponse struct {
	Entities []NEREntity `json:"entities"`
}

// NEREntity represents a detected entity. The NER service sets Suspicious and
// attaches any sanctions matches; the Go backend no longer string-matches on
// entity type (issue #4).
type NEREntity struct {
	Type             string          `json:"type"`
	Text             string          `json:"text"`
	Suspicious       bool            `json:"suspicious"`
	SanctionsMatches []SanctionMatch `json:"sanctions_matches,omitempty"`
}

// SanctionMatch describes a hit against the sanctions database.
type SanctionMatch struct {
	SanctionID   string  `json:"sanction_id"`
	Name         string  `json:"name"`
	MatchedAlias string  `json:"matched_alias,omitempty"`
	Similarity   float64 `json:"similarity"`
	RiskLevel    string  `json:"risk_level"`
	MatchType    string  `json:"match_type"`
}

// LLMRequest is the request body sent to the LLM service (/chat).
type LLMRequest struct {
	Text   string `json:"text"`
	Thread string `json:"thread,omitempty"`
}

// LLMResponse is the response from the LLM service.
type LLMResponse struct {
	Response string `json:"response"`
	ThreadID string `json:"thread_id"`
}

// ProcessTransactionRequest is the API request body.
type ProcessTransactionRequest struct {
	Transaction MarbleTransaction `json:"transaction"`
}

// ProcessTransactionResponse is the API response body.
type ProcessTransactionResponse struct {
	Processed      bool        `json:"processed"`
	Flagged        bool        `json:"flagged"`
	Reason         string      `json:"reason"`
	SARGenerated   bool        `json:"sar_generated,omitempty"`
	SARNarrative   string      `json:"sar_narrative,omitempty"`
	Entities       []NEREntity `json:"entities,omitempty"`
	ZKVerified     bool        `json:"zk_verified"`
	ProcessingTime float64     `json:"processing_time_ms"`
}

// TransactionEvent is published to Redis after processing.
type TransactionEvent struct {
	TransactionID    string      `json:"transaction_id"`
	Amount           float64     `json:"amount"`
	Currency         string      `json:"currency"`
	Sender           string      `json:"sender"`
	Receiver         string      `json:"receiver"`
	Flagged          bool        `json:"flagged"`
	Reason           string      `json:"reason"`
	SARGenerated     bool        `json:"sar_generated"`
	ZKVerified       bool        `json:"zk_verified"`
	Entities         []NEREntity `json:"entities,omitempty"`
	ProcessingTimeMs float64     `json:"processing_time_ms"`
	Timestamp        time.Time   `json:"timestamp"`
}

// Redis pub/sub channels.
const (
	channelTransactions = "trinity:transactions" // all processed transactions
	channelFlagged      = "trinity:flagged"      // flagged transactions only
	channelSARs         = "trinity:sars"         // SAR-generated events
)

// NewTransactionService opens the DB, connects to Redis and dials the ZK
// gRPC service. DATABASE_URL and REDIS_URL are required and cause a fast
// failure at startup if missing.
func NewTransactionService() (*TransactionService, error) {
	dbURL := os.Getenv("DATABASE_URL")
	if dbURL == "" {
		slog.Error("DATABASE_URL is required")
		os.Exit(1)
	}
	redisURL := os.Getenv("REDIS_URL")
	if redisURL == "" {
		slog.Error("REDIS_URL is required")
		os.Exit(1)
	}

	db, err := sql.Open("postgres", dbURL)
	if err != nil {
		return nil, fmt.Errorf("open database: %w", err)
	}
	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(5)
	db.SetConnMaxLifetime(5 * time.Minute)

	pingCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := db.PingContext(pingCtx); err != nil {
		return nil, fmt.Errorf("database ping: %w", err)
	}
	slog.Info("database connected")

	var rdb *redis.Client
	if strings.Contains(redisURL, "://") {
		opt, perr := redis.ParseURL(redisURL)
		if perr != nil {
			return nil, fmt.Errorf("parse redis url: %w", perr)
		}
		rdb = redis.NewClient(opt)
	} else {
		rdb = redis.NewClient(&redis.Options{Addr: redisURL})
	}
	if err := rdb.Ping(pingCtx).Err(); err != nil {
		return nil, fmt.Errorf("redis ping: %w", err)
	}
	slog.Info("redis connected", "url", redisURL)

	zkEndpoint := getEnv("ZK_ENDPOINT", "localhost:50053")
	zk, zkErr := NewZKClient(zkEndpoint)
	if zkErr != nil {
		slog.Warn("ZK service unavailable, continuing without ZK verification",
			"endpoint", zkEndpoint, "error", zkErr)
		zk = nil
	} else if perr := zk.Ping(3 * time.Second); perr != nil {
		slog.Warn("ZK service unreachable, continuing without ZK verification",
			"endpoint", zkEndpoint, "error", perr)
		// Keep the client so per-RPC calls can recover if ZK comes back later.
	} else {
		slog.Info("ZK service connected", "endpoint", zkEndpoint)
	}

	return &TransactionService{
		db:          db,
		rdb:         rdb,
		nerEndpoint: getEnv("NER_ENDPOINT", "http://localhost:9000"),
		llmEndpoint: getEnv("LLM_ENDPOINT", "http://localhost:8080"),
		zk:          zk,
	}, nil
}

// zkEndpoint returns the configured ZK address (for health/status reporting).
func (s *TransactionService) zkEndpoint() string {
	if s.zk != nil {
		return s.zk.Target()
	}
	return getEnv("ZK_ENDPOINT", "localhost:50053")
}

// =============================================================================
// ProcessTransaction — the Trinity compliance pipeline
// =============================================================================

// ProcessTransaction orchestrates: NER -> ZK (if suspicious) -> LLM (if
// suspicious) -> store -> publish.
func (s *TransactionService) ProcessTransaction(c *gin.Context) {
	start := time.Now()
	reqID := requestIDFromContext(c.Request.Context())

	var req ProcessTransactionRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		slog.Warn("invalid request body", "request_id", reqID, "error", err)
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	tx := req.Transaction
	slog.Info("processing transaction",
		"request_id", reqID, "id", tx.ID, "sender", tx.Sender, "amount", tx.Amount)

	// Step 1: NER identity resolution.
	entities, err := s.callNERService(c.Request.Context(), tx)
	if err != nil {
		slog.Error("NER service error", "request_id", reqID, "error", err)
		transactionsProcessedTotal.WithLabelValues("ner_error").Inc()
		c.JSON(http.StatusInternalServerError, gin.H{"error": "entity resolution failed"})
		return
	}
	slog.Info("NER complete", "request_id", reqID, "entities", len(entities))

	// Step 2: determine suspicion from the NER contract (entity.Suspicious).
	suspicious := isSuspicious(entities)

	var response ProcessTransactionResponse
	response.Processed = true
	response.Entities = entities

	if suspicious {
		// Step 3: ZK compliance proof verification (gRPC).
		zkVerified, zkErr := s.verifyWithZK(c.Request.Context(), tx, entities)
		if zkErr != nil {
			slog.Warn("ZK verification failed, continuing",
				"request_id", reqID, "error", zkErr)
		}
		response.ZKVerified = zkVerified
		slog.Info("ZK verification complete",
			"request_id", reqID, "verified", zkVerified)

		// Step 4: LLM SAR narrative.
		narrative, llmErr := s.callLLMService(c.Request.Context(), tx, entities)
		if llmErr != nil {
			slog.Error("LLM service error", "request_id", reqID, "error", llmErr)
			response.Flagged = true
			response.Reason = "Suspicious entities detected, LLM analysis failed"
		} else {
			response.Flagged = true
			response.Reason = "Suspicious entities detected, SAR generated"
			response.SARGenerated = true
			response.SARNarrative = narrative
		}
	} else {
		response.Flagged = false
		response.Reason = "Transaction passed all compliance checks"
	}

	response.ProcessingTime = float64(time.Since(start).Microseconds()) / 1000.0

	// Step 5: persist to PostgreSQL (trinity.transactions).
	if err := s.storeTransaction(tx, response); err != nil {
		slog.Error("failed to store transaction", "request_id", reqID, "error", err)
		// Continue — the response is still useful to the caller.
	}

	// Step 6: fan out to Redis pub/sub.
	s.publishEvents(c.Request.Context(), tx, response)

	// Metrics.
	transactionProcessingDuration.Observe(time.Since(start).Seconds())
	transactionsProcessedTotal.WithLabelValues("ok").Inc()
	if response.Flagged {
		transactionsFlaggedTotal.Inc()
	}
	if response.SARGenerated {
		sarsGeneratedTotal.Inc()
	}

	slog.Info("transaction processed",
		"request_id", reqID, "id", tx.ID, "flagged", response.Flagged,
		"sar", response.SARGenerated, "zk_verified", response.ZKVerified,
		"duration_ms", response.ProcessingTime)

	c.JSON(http.StatusOK, response)
}

// callNERService calls the Python NER service (/detect).
func (s *TransactionService) callNERService(ctx context.Context, tx MarbleTransaction) ([]NEREntity, error) {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()

	text := strings.TrimSpace(fmt.Sprintf("%s %s %s", tx.Description, tx.Sender, tx.Receiver))
	body, err := json.Marshal(map[string]string{"text": text})
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.nerEndpoint+"/detect", bytes.NewBuffer(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("NER service returned status %d", resp.StatusCode)
	}

	var nerResp NERResponse
	if err := json.NewDecoder(resp.Body).Decode(&nerResp); err != nil {
		return nil, err
	}
	return nerResp.Entities, nil
}

// callLLMService calls the LLM service (/chat) for a SAR narrative.
func (s *TransactionService) callLLMService(ctx context.Context, tx MarbleTransaction, entities []NEREntity) (string, error) {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()

	prompt := fmt.Sprintf(`Analyze this financial transaction for potential money laundering:

Transaction Details:
- ID: %s
- Amount: %.2f %s
- Description: %s
- Sender: %s
- Receiver: %s
- Time: %s

Detected Entities: %v

Provide a risk assessment and SAR narrative if suspicious.`,
		tx.ID, tx.Amount, tx.Currency, tx.Description, tx.Sender, tx.Receiver,
		tx.Timestamp.Format(time.RFC3339), entities)

	body, err := json.Marshal(LLMRequest{Text: prompt})
	if err != nil {
		return "", err
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, s.llmEndpoint+"/chat", bytes.NewBuffer(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("LLM service returned status %d", resp.StatusCode)
	}

	var llmResp LLMResponse
	if err := json.NewDecoder(resp.Body).Decode(&llmResp); err != nil {
		return "", err
	}
	return llmResp.Response, nil
}

// verifyWithZK calls the Rust ZK compliance service over gRPC. A short timeout
// is used so ZK downtime never blocks the pipeline. Errors are non-fatal.
func (s *TransactionService) verifyWithZK(ctx context.Context, tx MarbleTransaction, entities []NEREntity) (bool, error) {
	if s.zk == nil {
		return false, nil
	}

	publicInputs, err := json.Marshal(map[string]any{
		"transaction_id":      tx.ID,
		"amount":              tx.Amount,
		"currency":            tx.Currency,
		"sender":              tx.Sender,
		"receiver":            tx.Receiver,
		"suspicious_entities": entities,
	})
	if err != nil {
		return false, fmt.Errorf("marshal public inputs: %w", err)
	}

	zkCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()

	resp, err := s.zk.VerifyComplianceProof(zkCtx, &VerifyProofRequest{
		PublicInputs: publicInputs,
	})
	if err != nil {
		return false, err
	}
	if resp.Error != "" {
		return false, fmt.Errorf("zk service error: %s", resp.Error)
	}
	return resp.IsValid, nil
}

// isSuspicious reports whether any NER entity was flagged suspicious by the
// NER service. The Go backend no longer hardcodes suspicious names (issue #4).
func isSuspicious(entities []NEREntity) bool {
	for _, e := range entities {
		if e.Suspicious {
			return true
		}
	}
	return false
}

// storeTransaction persists the transaction and processing outcome to the
// trinity.transactions table (issue #5: schema-qualified, id is VARCHAR PK).
func (s *TransactionService) storeTransaction(tx MarbleTransaction, response ProcessTransactionResponse) error {
	const query = `
		INSERT INTO trinity.transactions (
			id, amount, currency, description, sender, receiver, timestamp,
			status, category, flagged, reason, sar_generated, sar_narrative,
			processing_time_ms, zk_verified
		) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
		ON CONFLICT (id) DO UPDATE SET
			amount = EXCLUDED.amount,
			currency = EXCLUDED.currency,
			description = EXCLUDED.description,
			sender = EXCLUDED.sender,
			receiver = EXCLUDED.receiver,
			timestamp = EXCLUDED.timestamp,
			status = EXCLUDED.status,
			category = EXCLUDED.category,
			flagged = EXCLUDED.flagged,
			reason = EXCLUDED.reason,
			sar_generated = EXCLUDED.sar_generated,
			sar_narrative = EXCLUDED.sar_narrative,
			processing_time_ms = EXCLUDED.processing_time_ms,
			zk_verified = EXCLUDED.zk_verified
	`
	_, err := s.db.Exec(query,
		tx.ID, tx.Amount, tx.Currency, tx.Description, tx.Sender, tx.Receiver,
		tx.Timestamp, tx.Status, tx.Category, response.Flagged, response.Reason,
		response.SARGenerated, response.SARNarrative, response.ProcessingTime,
		response.ZKVerified,
	)
	return err
}

// publishEvents fans the processed transaction out to Redis pub/sub channels.
func (s *TransactionService) publishEvents(ctx context.Context, tx MarbleTransaction, resp ProcessTransactionResponse) {
	event := TransactionEvent{
		TransactionID:    tx.ID,
		Amount:           tx.Amount,
		Currency:         tx.Currency,
		Sender:           tx.Sender,
		Receiver:         tx.Receiver,
		Flagged:          resp.Flagged,
		Reason:           resp.Reason,
		SARGenerated:     resp.SARGenerated,
		ZKVerified:       resp.ZKVerified,
		Entities:         resp.Entities,
		ProcessingTimeMs: resp.ProcessingTime,
		Timestamp:        time.Now().UTC(),
	}
	payload, err := json.Marshal(event)
	if err != nil {
		slog.Error("failed to marshal transaction event", "error", err)
		return
	}

	if err := s.rdb.Publish(ctx, channelTransactions, payload).Err(); err != nil {
		slog.Warn("failed to publish to trinity:transactions", "error", err)
	}
	if resp.Flagged {
		if err := s.rdb.Publish(ctx, channelFlagged, payload).Err(); err != nil {
			slog.Warn("failed to publish to trinity:flagged", "error", err)
		}
	}
	if resp.SARGenerated {
		sarPayload, _ := json.Marshal(map[string]any{
			"transaction_id": tx.ID,
			"reason":         resp.Reason,
			"sar_narrative":  resp.SARNarrative,
			"timestamp":      time.Now().UTC(),
		})
		if err := s.rdb.Publish(ctx, channelSARs, sarPayload).Err(); err != nil {
			slog.Warn("failed to publish to trinity:sars", "error", err)
		}
	}
}

// GetStatistics returns real aggregate metrics from trinity.transactions.
func (s *TransactionService) GetStatistics(c *gin.Context) {
	var total, flagged, sars int64
	var avgProcessing float64
	err := s.db.QueryRowContext(c.Request.Context(), `
		SELECT
			COUNT(*),
			COUNT(*) FILTER (WHERE flagged),
			COUNT(*) FILTER (WHERE sar_generated),
			COALESCE(AVG(processing_time_ms), 0)
		FROM trinity.transactions
	`).Scan(&total, &flagged, &sars, &avgProcessing)
	if err != nil {
		slog.Error("failed to query statistics", "error", err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": "failed to query statistics"})
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"total_transactions":     total,
		"flagged_transactions":   flagged,
		"sars_generated":         sars,
		"avg_processing_time_ms": avgProcessing,
		"ner_endpoint":           s.nerEndpoint,
		"llm_endpoint":           s.llmEndpoint,
		"zk_endpoint":            s.zkEndpoint(),
	})
}

// Health checks DB + Redis connectivity and reports ZK availability.
func (s *TransactionService) Health(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
	defer cancel()

	dbOK := s.db.PingContext(ctx) == nil
	redisOK := s.rdb.Ping(ctx).Err() == nil
	zkConnected := s.zk != nil

	healthy := dbOK && redisOK
	status := "healthy"
	code := http.StatusOK
	if !healthy {
		status = "unhealthy"
		code = http.StatusServiceUnavailable
	}

	c.JSON(code, gin.H{
		"status":       status,
		"database":     statusStr(dbOK),
		"redis":        statusStr(redisOK),
		"zk_connected": zkConnected,
		"zk_endpoint":  s.zkEndpoint(),
		"ner_endpoint": s.nerEndpoint,
		"llm_endpoint": s.llmEndpoint,
	})
}

type requestIDKey struct{}

func requestIDMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		rid := c.GetHeader("X-Request-ID")
		if rid == "" {
			rid = newRequestID()
		}
		c.Request = c.Request.WithContext(
			context.WithValue(c.Request.Context(), requestIDKey{}, rid),
		)
		c.Header("X-Request-ID", rid)
		c.Next()
	}
}

func loggingMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()
		slog.Info("http request",
			"request_id", requestIDFromContext(c.Request.Context()),
			"method", c.Request.Method,
			"path", c.Request.URL.Path,
			"status", c.Writer.Status(),
			"duration_ms", float64(time.Since(start).Microseconds())/1000.0,
			"ip", c.ClientIP(),
		)
	}
}

func requestIDFromContext(ctx context.Context) string {
	if v, ok := ctx.Value(requestIDKey{}).(string); ok {
		return v
	}
	return ""
}

// newRequestID returns a 32-char hex identifier.
func newRequestID() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		// Fallback to a time-based identifier if the CSPRNG is unavailable.
		return fmt.Sprintf("tx-%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(b)
}

func getEnv(key, defaultValue string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return defaultValue
}

func statusStr(ok bool) string {
	if ok {
		return "connected"
	}
	return "disconnected"
}

func main() {
	// Structured JSON logging (log/slog, stdlib in Go 1.27).
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, nil)))

	service, err := NewTransactionService()
	if err != nil {
		slog.Error("failed to initialize service", "error", err)
		os.Exit(1)
	}
	defer service.db.Close()
	defer service.rdb.Close()
	if service.zk != nil {
		defer service.zk.Close()
	}

	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(gin.Recovery())
	router.Use(requestIDMiddleware())
	router.Use(loggingMiddleware())

	api := router.Group("/api/v1")
	{
		api.POST("/transactions/process", service.ProcessTransaction)
		api.GET("/statistics", service.GetStatistics)
		api.GET("/health", service.Health)
	}

	// Prometheus metrics endpoint.
	router.GET("/metrics", gin.WrapH(promhttp.Handler()))

	port := getEnv("PORT", "8080")
	slog.Info("Trinity Guard Backend starting",
		"port", port,
		"ner_endpoint", service.nerEndpoint,
		"llm_endpoint", service.llmEndpoint,
		"zk_endpoint", service.zkEndpoint(),
	)
	slog.Info("API endpoints",
		"process", "POST /api/v1/transactions/process",
		"statistics", "GET /api/v1/statistics",
		"health", "GET /api/v1/health",
		"metrics", "GET /metrics",
	)

	if err := router.Run(":" + port); err != nil {
		slog.Error("failed to start server", "error", err)
		os.Exit(1)
	}
}
