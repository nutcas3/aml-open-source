package main

// main_test.go — Tests for the Trinity Guard Go backend.
//
// NER and LLM are exercised via httptest servers. Redis pub/sub is exercised
// via an in-process miniredis server. Database-dependent tests (ProcessTransaction
// end-to-end, Health) use a real PostgreSQL when DATABASE_URL is set and are
// skipped otherwise — keeping with the "real implementations, no mocks" rule
// for the data layer.

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
)

// -----------------------------------------------------------------------------
// Test harness
// -----------------------------------------------------------------------------

func TestMain(m *testing.M) {
	gin.SetMode(gin.TestMode)
	os.Exit(m.Run())
}

// testService builds a TransactionService wired to httptest NER/LLM servers and
// a miniredis-backed Redis client. The DB is only set when DATABASE_URL is
// available so DB-free tests can run anywhere.
func testService(t *testing.T, nerURL, llmURL string, rdb *redis.Client) *TransactionService {
	t.Helper()
	svc := &TransactionService{
		nerEndpoint: nerURL,
		llmEndpoint: llmURL,
		rdb:         rdb,
	}
	if dsn := os.Getenv("DATABASE_URL"); dsn != "" {
		db, err := sql.Open("postgres", dsn)
		if err != nil {
			t.Fatalf("open db: %v", err)
		}
		svc.db = db
		t.Cleanup(func() { db.Close() })
	}
	return svc
}

func newMiniRedis(t *testing.T) (*miniredis.Miniredis, *redis.Client) {
	t.Helper()
	mr, err := miniredis.Run()
	if err != nil {
		t.Fatalf("start miniredis: %v", err)
	}
	t.Cleanup(mr.Close)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	t.Cleanup(func() { rdb.Close() })
	return mr, rdb
}

// requireDB skips the test when no real PostgreSQL is configured.
func requireDB(t *testing.T, svc *TransactionService) {
	t.Helper()
	if svc.db == nil {
		t.Skip("DATABASE_URL not set; skipping DB-dependent test")
	}
}

func setupRouter(svc *TransactionService) *gin.Engine {
	r := gin.New()
	r.POST("/process", svc.ProcessTransaction)
	r.GET("/health", svc.Health)
	r.GET("/statistics", svc.GetStatistics)
	return r
}

// subscribeConfirmed subscribes to the given channels and consumes the
// subscription confirmations so the caller is guaranteed to be subscribed before
// publishing. It uses a timeout so a misbehaving server cannot hang the test.
func subscribeConfirmed(t *testing.T, rdb *redis.Client, channels ...string) *redis.PubSub {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	ps := rdb.Subscribe(ctx, channels...)
	for i := 0; i < len(channels); i++ {
		if _, err := ps.Receive(ctx); err != nil {
			t.Fatalf("subscribe confirm on %v: %v", channels, err)
		}
	}
	return ps
}

// -----------------------------------------------------------------------------
// isSuspicious (pure)
// -----------------------------------------------------------------------------

func TestIsSuspicious(t *testing.T) {
	tests := []struct {
		name     string
		entities []NEREntity
		want     bool
	}{
		{
			name:     "empty",
			entities: nil,
			want:     false,
		},
		{
			name: "none suspicious",
			entities: []NEREntity{
				{Type: "Person", Text: "Alice"},
				{Type: "Company", Text: "Acme", Suspicious: false},
			},
			want: false,
		},
		{
			name: "one suspicious",
			entities: []NEREntity{
				{Type: "Person", Text: "M. Emmanuel", Suspicious: true,
					SanctionsMatches: []SanctionMatch{{SanctionID: "sanction_001", Name: "M. Emmanuel", RiskLevel: "HIGH", Similarity: 0.98}}},
			},
			want: true,
		},
		{
			name: "suspicious among many",
			entities: []NEREntity{
				{Type: "Country", Text: "Kenya"},
				{Type: "Person", Text: "Bob"},
				{Type: "Company", Text: "Moneycorp", Suspicious: true},
			},
			want: true,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := isSuspicious(tc.entities); got != tc.want {
				t.Fatalf("isSuspicious = %v, want %v", got, tc.want)
			}
		})
	}
}

// -----------------------------------------------------------------------------
// callNERService (httptest)
// -----------------------------------------------------------------------------

func TestCallNERService(t *testing.T) {
	ner := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/detect" || r.Method != http.MethodPost {
			t.Errorf("unexpected NER request: %s %s", r.Method, r.URL.Path)
		}
		var body map[string]string
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode NER body: %v", err)
		}
		if body["text"] == "" {
			t.Error("NER request missing text")
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(NERResponse{
			Entities: []NEREntity{
				{Type: "Person", Text: "M. Emmanuel", Suspicious: true,
					SanctionsMatches: []SanctionMatch{{SanctionID: "s1", Name: "M. Emmanuel", RiskLevel: "HIGH", Similarity: 0.97}}},
				{Type: "Company", Text: "Acme"},
			},
		})
	}))
	defer ner.Close()

	svc := &TransactionService{nerEndpoint: ner.URL}
	entities, err := svc.callNERService(context.Background(), MarbleTransaction{
		Description: "wire transfer", Sender: "Alice", Receiver: "M. Emmanuel",
	})
	if err != nil {
		t.Fatalf("callNERService: %v", err)
	}
	if len(entities) != 2 {
		t.Fatalf("expected 2 entities, got %d", len(entities))
	}
	if !entities[0].Suspicious {
		t.Error("expected first entity to be suspicious")
	}
	if entities[0].SanctionsMatches[0].SanctionID != "s1" {
		t.Errorf("expected sanction id s1, got %q", entities[0].SanctionsMatches[0].SanctionID)
	}
	if entities[1].Suspicious {
		t.Error("expected second entity to be non-suspicious")
	}
}

func TestCallNERService_ErrorStatus(t *testing.T) {
	ner := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer ner.Close()

	svc := &TransactionService{nerEndpoint: ner.URL}
	if _, err := svc.callNERService(context.Background(), MarbleTransaction{}); err == nil {
		t.Fatal("expected error on non-200 NER response")
	}
}

// -----------------------------------------------------------------------------
// callLLMService (httptest)
// -----------------------------------------------------------------------------

func TestCallLLMService(t *testing.T) {
	llm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/chat" {
			t.Errorf("unexpected LLM path: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(LLMResponse{Response: "SAR narrative: high risk", ThreadID: "t1"})
	}))
	defer llm.Close()

	svc := &TransactionService{llmEndpoint: llm.URL}
	out, err := svc.callLLMService(context.Background(), MarbleTransaction{ID: "tx-1", Amount: 9000}, []NEREntity{{Text: "M. Emmanuel", Suspicious: true}})
	if err != nil {
		t.Fatalf("callLLMService: %v", err)
	}
	if out != "SAR narrative: high risk" {
		t.Fatalf("unexpected LLM response: %q", out)
	}
}

// -----------------------------------------------------------------------------
// publishEvents (miniredis pub/sub)
// -----------------------------------------------------------------------------

func TestPublishEvents_FlaggedWithSAR(t *testing.T) {
	_, rdb := newMiniRedis(t)
	svc := &TransactionService{rdb: rdb}

	ps := subscribeConfirmed(t, rdb, channelTransactions, channelFlagged, channelSARs)
	defer ps.Close()

	resp := ProcessTransactionResponse{
		Processed:    true,
		Flagged:      true,
		Reason:       "Suspicious entities detected, SAR generated",
		SARGenerated: true,
		SARNarrative: "narrative",
		ZKVerified:   true,
	}
	svc.publishEvents(context.Background(), MarbleTransaction{ID: "tx-1", Amount: 5000, Currency: "USD", Sender: "A", Receiver: "B"}, resp)

	got := map[string]int{}
	readCtx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	for {
		m, err := ps.ReceiveMessage(readCtx)
		if err != nil {
			break // timeout
		}
		got[m.Channel]++
		if got[channelTransactions] > 0 && got[channelFlagged] > 0 && got[channelSARs] > 0 {
			break
		}
	}

	if got[channelTransactions] != 1 {
		t.Errorf("trinity:transactions messages = %d, want 1", got[channelTransactions])
	}
	if got[channelFlagged] != 1 {
		t.Errorf("trinity:flagged messages = %d, want 1", got[channelFlagged])
	}
	if got[channelSARs] != 1 {
		t.Errorf("trinity:sars messages = %d, want 1", got[channelSARs])
	}
}

func TestPublishEvents_NotFlagged(t *testing.T) {
	_, rdb := newMiniRedis(t)
	svc := &TransactionService{rdb: rdb}

	ps := subscribeConfirmed(t, rdb, channelTransactions, channelFlagged, channelSARs)
	defer ps.Close()

	svc.publishEvents(context.Background(), MarbleTransaction{ID: "tx-2"}, ProcessTransactionResponse{
		Processed: true,
		Flagged:   false,
		Reason:    "Transaction passed all compliance checks",
	})

	got := map[string]int{}
	readCtx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	for {
		m, err := ps.ReceiveMessage(readCtx)
		if err != nil {
			break
		}
		got[m.Channel]++
	}

	if got[channelTransactions] != 1 {
		t.Errorf("trinity:transactions messages = %d, want 1", got[channelTransactions])
	}
	if got[channelFlagged] != 0 {
		t.Errorf("trinity:flagged messages = %d, want 0", got[channelFlagged])
	}
	if got[channelSARs] != 0 {
		t.Errorf("trinity:sars messages = %d, want 0", got[channelSARs])
	}
}

// -----------------------------------------------------------------------------
// ProcessTransaction end-to-end (httptest NER/LLM + miniredis + real DB)
// -----------------------------------------------------------------------------

func newNERServer(t *testing.T, suspicious bool) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		entities := []NEREntity{
			{Type: "Person", Text: "Alice"},
			{Type: "Person", Text: "M. Emmanuel"},
		}
		if suspicious {
			entities[1].Suspicious = true
			entities[1].SanctionsMatches = []SanctionMatch{{
				SanctionID: "sanction_001", Name: "M. Emmanuel", RiskLevel: "HIGH", Similarity: 0.99,
			}}
		}
		_ = json.NewEncoder(w).Encode(NERResponse{Entities: entities})
	}))
}

func newLLMServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(LLMResponse{Response: "SAR: suspicious wire to sanctioned entity", ThreadID: "t-1"})
	}))
}

func doProcess(router *gin.Engine, tx MarbleTransaction) (*http.Response, ProcessTransactionResponse) {
	body, _ := json.Marshal(ProcessTransactionRequest{Transaction: tx})
	req := httptest.NewRequest(http.MethodPost, "/process", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)
	var resp ProcessTransactionResponse
	_ = json.NewDecoder(w.Body).Decode(&resp)
	return w.Result(), resp
}

func TestProcessTransaction_NonSuspicious(t *testing.T) {
	ner := newNERServer(t, false)
	defer ner.Close()
	llm := newLLMServer(t)
	defer llm.Close()
	_, rdb := newMiniRedis(t)
	svc := testService(t, ner.URL, llm.URL, rdb)
	requireDB(t, svc)

	// Subscribe to verify only the all-transactions channel fires.
	ps := subscribeConfirmed(t, rdb, channelTransactions, channelFlagged, channelSARs)
	defer ps.Close()

	router := setupRouter(svc)
	tx := MarbleTransaction{
		ID: "test-tx-clean", Amount: 100, Currency: "USD", Sender: "Alice", Receiver: "Bob",
		Timestamp: time.Now().UTC(), Status: "pending", Category: "transfer",
	}
	httpResp, resp := doProcess(router, tx)
	defer httpResp.Body.Close()

	if httpResp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", httpResp.StatusCode)
	}
	if !resp.Processed {
		t.Error("expected processed=true")
	}
	if resp.Flagged {
		t.Error("expected flagged=false for non-suspicious transaction")
	}
	if resp.SARGenerated {
		t.Error("expected sar_generated=false")
	}
	if resp.ZKVerified {
		t.Error("expected zk_verified=false (ZK not called for non-suspicious)")
	}
	if len(resp.Entities) != 2 {
		t.Fatalf("expected 2 entities, got %d", len(resp.Entities))
	}

	// Verify Redis: only trinity:transactions should have a message.
	got := map[string]int{}
	readCtx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	for {
		m, err := ps.ReceiveMessage(readCtx)
		if err != nil {
			break
		}
		got[m.Channel]++
	}
	if got[channelTransactions] != 1 {
		t.Errorf("trinity:transactions = %d, want 1", got[channelTransactions])
	}
	if got[channelFlagged] != 0 || got[channelSARs] != 0 {
		t.Errorf("unexpected flagged/sars messages: flagged=%d sars=%d", got[channelFlagged], got[channelSARs])
	}
}

func TestProcessTransaction_Suspicious(t *testing.T) {
	ner := newNERServer(t, true)
	defer ner.Close()
	llm := newLLMServer(t)
	defer llm.Close()
	_, rdb := newMiniRedis(t)
	svc := testService(t, ner.URL, llm.URL, rdb)
	requireDB(t, svc)
	// ZK client is nil in tests -> verifyWithZK returns (false, nil) and the
	// pipeline continues (matching the "ZK unreachable -> log + continue" rule).

	ps := subscribeConfirmed(t, rdb, channelTransactions, channelFlagged, channelSARs)
	defer ps.Close()

	router := setupRouter(svc)
	tx := MarbleTransaction{
		ID: "test-tx-suspicious", Amount: 25000, Currency: "USD", Sender: "Alice", Receiver: "M. Emmanuel",
		Timestamp: time.Now().UTC(), Status: "pending", Category: "transfer",
		Description: "offshore wire",
	}
	httpResp, resp := doProcess(router, tx)
	defer httpResp.Body.Close()

	if httpResp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", httpResp.StatusCode)
	}
	if !resp.Processed {
		t.Error("expected processed=true")
	}
	if !resp.Flagged {
		t.Error("expected flagged=true for suspicious transaction")
	}
	if !resp.SARGenerated {
		t.Error("expected sar_generated=true")
	}
	if resp.SARNarrative == "" {
		t.Error("expected non-empty sar narrative")
	}
	// ZK is nil in the test harness, so verification is false but not fatal.
	if resp.ZKVerified {
		t.Error("expected zk_verified=false when ZK client is absent")
	}

	// All three Redis channels should have received a message.
	got := map[string]int{}
	readCtx, cancel := context.WithTimeout(context.Background(), time.Second)
	defer cancel()
	for {
		m, err := ps.ReceiveMessage(readCtx)
		if err != nil {
			break
		}
		got[m.Channel]++
		if got[channelTransactions] > 0 && got[channelFlagged] > 0 && got[channelSARs] > 0 {
			break
		}
	}
	if got[channelTransactions] != 1 || got[channelFlagged] != 1 || got[channelSARs] != 1 {
		t.Errorf("redis messages = %+v, want one on each channel", got)
	}
}

func TestProcessTransaction_NERError(t *testing.T) {
	ner := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer ner.Close()
	llm := newLLMServer(t)
	defer llm.Close()
	_, rdb := newMiniRedis(t)
	svc := testService(t, ner.URL, llm.URL, rdb)
	// No DB needed: the pipeline returns at the NER step before storeTransaction.

	router := setupRouter(svc)
	body, _ := json.Marshal(ProcessTransactionRequest{Transaction: MarbleTransaction{ID: "x"}})
	req := httptest.NewRequest(http.MethodPost, "/process", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want 500", w.Code)
	}
}

// -----------------------------------------------------------------------------
// Health (miniredis + real DB)
// -----------------------------------------------------------------------------

func TestHealth_Healthy(t *testing.T) {
	_, rdb := newMiniRedis(t)
	svc := testService(t, "", "", rdb)
	requireDB(t, svc)

	router := setupRouter(svc)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", w.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode health: %v", err)
	}
	if body["status"] != "healthy" {
		t.Errorf("status = %v, want healthy", body["status"])
	}
	if body["database"] != "connected" {
		t.Errorf("database = %v, want connected", body["database"])
	}
	if body["redis"] != "connected" {
		t.Errorf("redis = %v, want connected", body["redis"])
	}
}

func TestHealth_RedisDown(t *testing.T) {
	_, rdb := newMiniRedis(t)
	svc := testService(t, "", "", rdb)
	requireDB(t, svc)

	// Close miniredis to simulate Redis being down.
	rdb.Close()

	router := setupRouter(svc)
	req := httptest.NewRequest(http.MethodGet, "/health", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", w.Code)
	}
	body, _ := io.ReadAll(w.Body)
	if !bytes.Contains(body, []byte("unhealthy")) {
		t.Errorf("expected unhealthy body, got %s", body)
	}
}

// -----------------------------------------------------------------------------
// ZK codec round-trip (no network)
// -----------------------------------------------------------------------------

func TestTrinityCodec_VerifyProofRoundTrip(t *testing.T) {
	c := trinityCodec{}
	req := &VerifyProofRequest{
		ProofBytes:   []byte{0x01, 0x02, 0x03},
		PublicInputs: []byte("public-data"),
	}
	data, err := c.Marshal(req)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got VerifyProofRequest
	if err := c.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if !bytes.Equal(got.ProofBytes, req.ProofBytes) {
		t.Errorf("proof_bytes = %v, want %v", got.ProofBytes, req.ProofBytes)
	}
	if !bytes.Equal(got.PublicInputs, req.PublicInputs) {
		t.Errorf("public_inputs = %q, want %q", got.PublicInputs, req.PublicInputs)
	}
}

func TestTrinityCodec_VerifyProofResponseRoundTrip(t *testing.T) {
	c := trinityCodec{}
	resp := &VerifyProofResponse{IsValid: true, Error: "bad proof"}
	data, err := c.Marshal(resp)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got VerifyProofResponse
	if err := c.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.IsValid != true {
		t.Errorf("is_valid = %v, want true", got.IsValid)
	}
	if got.Error != "bad proof" {
		t.Errorf("error = %q, want %q", got.Error, "bad proof")
	}
}

func TestTrinityCodec_HealthResponseRoundTrip(t *testing.T) {
	c := trinityCodec{}
	resp := &HealthResponse{Status: "ok", Version: "0.1.0", CircuitLoaded: true}
	data, err := c.Marshal(resp)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got HealthResponse
	if err := c.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if got.Status != "ok" || got.Version != "0.1.0" || !got.CircuitLoaded {
		t.Errorf("round-trip mismatch: %+v", got)
	}
}

func TestTrinityCodec_MerkleProofRoundTrip(t *testing.T) {
	c := trinityCodec{}
	req := &MerkleProofRequest{
		Leaf:  []byte("leaf"),
		Proof: [][]byte{[]byte("p1"), []byte("p2")},
		Root:  []byte("root"),
	}
	data, err := c.Marshal(req)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var got MerkleProofRequest
	if err := c.Unmarshal(data, &got); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if !bytes.Equal(got.Leaf, req.Leaf) || !bytes.Equal(got.Root, req.Root) {
		t.Errorf("leaf/root mismatch: %+v", got)
	}
	if len(got.Proof) != 2 || !bytes.Equal(got.Proof[0], []byte("p1")) || !bytes.Equal(got.Proof[1], []byte("p2")) {
		t.Errorf("proof mismatch: %+v", got.Proof)
	}
}
