package main

// zk_client.go — Manual gRPC client for the Rust ZK compliance service.
//
// The protobuf contract lives in contracts/proto/trinity.proto. Because we
// cannot run `protoc` in this environment, the message types and the gRPC
// service client are hand-written here to match that contract exactly.
//
// Wire compatibility is achieved by encoding/decoding the protobuf binary
// format directly via google.golang.org/protobuf/encoding/protowire, using
// only field numbers (which is all the wire format carries). A custom
// grpc/encoding.Codec named "proto" is registered so the on-the-wire
// content-type remains "application/grpc+proto" (what tonic/Rust expects)
// while our hand-rolled marshalers are used instead of generated code.

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/encoding"
	"google.golang.org/protobuf/encoding/protowire"
)

// =============================================================================
// Proto message types (matching contracts/proto/trinity.proto)
// =============================================================================

// VerifyProofRequest: { bytes proof_bytes = 1; bytes public_inputs = 2; }
type VerifyProofRequest struct {
	ProofBytes   []byte
	PublicInputs []byte
}

// VerifyProofResponse: { bool is_valid = 1; string error = 2; }
type VerifyProofResponse struct {
	IsValid bool
	Error   string
}

// GenerateProofRequest: { bytes private_data = 1; bytes public_data = 2; }
type GenerateProofRequest struct {
	PrivateData []byte
	PublicData  []byte
}

// GenerateProofResponse: { bytes proof_bytes = 1; string error = 2; }
type GenerateProofResponse struct {
	ProofBytes []byte
	Error      string
}

// HashRequest: { bytes data = 1; }
type HashRequest struct {
	Data []byte
}

// HashResponse: { bytes hash = 1; string error = 2; }
type HashResponse struct {
	Hash  []byte
	Error string
}

// MerkleProofRequest: { bytes leaf = 1; repeated bytes proof = 2; bytes root = 3; }
type MerkleProofRequest struct {
	Leaf  []byte
	Proof [][]byte
	Root  []byte
}

// MerkleProofResponse: { bool is_valid = 1; string error = 2; }
type MerkleProofResponse struct {
	IsValid bool
	Error   string
}

// HealthRequest: {} (empty)
type HealthRequest struct{}

// HealthResponse: { string status = 1; string version = 2; bool circuit_loaded = 3; }
type HealthResponse struct {
	Status        string
	Version       string
	CircuitLoaded bool
}

// =============================================================================
// Protobuf wire (de)serialization helpers
// =============================================================================

func putBytes(b []byte, num protowire.Number, val []byte) []byte {
	if len(val) == 0 {
		return b
	}
	b = protowire.AppendTag(b, num, protowire.BytesType)
	b = protowire.AppendBytes(b, val)
	return b
}

func putString(b []byte, num protowire.Number, val string) []byte {
	if val == "" {
		return b
	}
	b = protowire.AppendTag(b, num, protowire.BytesType)
	b = protowire.AppendString(b, val)
	return b
}

func putBool(b []byte, num protowire.Number, val bool) []byte {
	if !val {
		return b
	}
	b = protowire.AppendTag(b, num, protowire.VarintType)
	b = protowire.AppendVarint(b, 1)
	return b
}

// readField parses one protobuf field record. For BytesType it returns the
// field content in raw; for VarintType it returns the decoded value in uval.
// Returns n < 0 on a parse error. Unsupported wire types are skipped (forward
// compatibility with future proto additions).
func readField(b []byte) (num protowire.Number, wt protowire.Type, raw []byte, uval uint64, n int) {
	num, wt, tn := protowire.ConsumeTag(b)
	if tn < 0 {
		return 0, 0, nil, 0, tn
	}
	body := b[tn:]
	switch wt {
	case protowire.BytesType:
		raw, n = protowire.ConsumeBytes(body)
		if n < 0 {
			return 0, 0, nil, 0, n
		}
		return num, wt, raw, 0, tn + n
	case protowire.VarintType:
		uval, n = protowire.ConsumeVarint(body)
		if n < 0 {
			return 0, 0, nil, 0, n
		}
		return num, wt, nil, uval, tn + n
	default:
		n = protowire.ConsumeFieldValue(num, wt, body)
		if n < 0 {
			return 0, 0, nil, 0, n
		}
		return num, wt, nil, 0, tn + n
	}
}

// =============================================================================
// Marshal / Unmarshal per message
// =============================================================================

func (m *VerifyProofRequest) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.ProofBytes)
	b = putBytes(b, 2, m.PublicInputs)
	return b
}

func (m *VerifyProofRequest) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		if wt != protowire.BytesType {
			continue
		}
		switch num {
		case 1:
			m.ProofBytes = append([]byte(nil), raw...)
		case 2:
			m.PublicInputs = append([]byte(nil), raw...)
		}
	}
}

func (m *VerifyProofResponse) marshal() []byte {
	var b []byte
	b = putBool(b, 1, m.IsValid)
	b = putString(b, 2, m.Error)
	return b
}

func (m *VerifyProofResponse) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, uval, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		switch num {
		case 1:
			if wt == protowire.VarintType {
				m.IsValid = uval != 0
			}
		case 2:
			if wt == protowire.BytesType {
				m.Error = string(raw)
			}
		}
	}
}

func (m *GenerateProofRequest) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.PrivateData)
	b = putBytes(b, 2, m.PublicData)
	return b
}

func (m *GenerateProofRequest) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		if wt != protowire.BytesType {
			continue
		}
		switch num {
		case 1:
			m.PrivateData = append([]byte(nil), raw...)
		case 2:
			m.PublicData = append([]byte(nil), raw...)
		}
	}
}

func (m *GenerateProofResponse) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.ProofBytes)
	b = putString(b, 2, m.Error)
	return b
}

func (m *GenerateProofResponse) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		switch num {
		case 1:
			if wt == protowire.BytesType {
				m.ProofBytes = append([]byte(nil), raw...)
			}
		case 2:
			if wt == protowire.BytesType {
				m.Error = string(raw)
			}
		}
	}
}

func (m *HashRequest) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.Data)
	return b
}

func (m *HashRequest) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		if num == 1 && wt == protowire.BytesType {
			m.Data = append([]byte(nil), raw...)
		}
	}
}

func (m *HashResponse) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.Hash)
	b = putString(b, 2, m.Error)
	return b
}

func (m *HashResponse) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		switch num {
		case 1:
			if wt == protowire.BytesType {
				m.Hash = append([]byte(nil), raw...)
			}
		case 2:
			if wt == protowire.BytesType {
				m.Error = string(raw)
			}
		}
	}
}

func (m *MerkleProofRequest) marshal() []byte {
	var b []byte
	b = putBytes(b, 1, m.Leaf)
	for _, p := range m.Proof {
		b = protowire.AppendTag(b, 2, protowire.BytesType)
		b = protowire.AppendBytes(b, p)
	}
	b = putBytes(b, 3, m.Root)
	return b
}

func (m *MerkleProofRequest) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, _, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		if wt != protowire.BytesType {
			continue
		}
		switch num {
		case 1:
			m.Leaf = append([]byte(nil), raw...)
		case 2:
			m.Proof = append(m.Proof, append([]byte(nil), raw...))
		case 3:
			m.Root = append([]byte(nil), raw...)
		}
	}
}

func (m *MerkleProofResponse) marshal() []byte {
	var b []byte
	b = putBool(b, 1, m.IsValid)
	b = putString(b, 2, m.Error)
	return b
}

func (m *MerkleProofResponse) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, uval, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		switch num {
		case 1:
			if wt == protowire.VarintType {
				m.IsValid = uval != 0
			}
		case 2:
			if wt == protowire.BytesType {
				m.Error = string(raw)
			}
		}
	}
}

func (m *HealthRequest) marshal() []byte { return nil }

func (m *HealthRequest) unmarshal(data []byte) {}

func (m *HealthResponse) marshal() []byte {
	var b []byte
	b = putString(b, 1, m.Status)
	b = putString(b, 2, m.Version)
	b = putBool(b, 3, m.CircuitLoaded)
	return b
}

func (m *HealthResponse) unmarshal(data []byte) {
	for len(data) > 0 {
		num, wt, raw, uval, n := readField(data)
		if n < 0 {
			return
		}
		data = data[n:]
		switch num {
		case 1:
			if wt == protowire.BytesType {
				m.Status = string(raw)
			}
		case 2:
			if wt == protowire.BytesType {
				m.Version = string(raw)
			}
		case 3:
			if wt == protowire.VarintType {
				m.CircuitLoaded = uval != 0
			}
		}
	}
}

// =============================================================================
// gRPC codec — hand-rolled protobuf over the standard "proto" content subtype
// =============================================================================

type trinityCodec struct{}

func (trinityCodec) Name() string { return "proto" }

func (trinityCodec) Marshal(v any) ([]byte, error) {
	switch m := v.(type) {
	case *VerifyProofRequest:
		return m.marshal(), nil
	case *VerifyProofResponse:
		return m.marshal(), nil
	case *GenerateProofRequest:
		return m.marshal(), nil
	case *GenerateProofResponse:
		return m.marshal(), nil
	case *HashRequest:
		return m.marshal(), nil
	case *HashResponse:
		return m.marshal(), nil
	case *MerkleProofRequest:
		return m.marshal(), nil
	case *MerkleProofResponse:
		return m.marshal(), nil
	case *HealthRequest:
		return m.marshal(), nil
	case *HealthResponse:
		return m.marshal(), nil
	default:
		return nil, fmt.Errorf("trinityCodec: unsupported message type %T", v)
	}
}

func (trinityCodec) Unmarshal(data []byte, v any) error {
	switch m := v.(type) {
	case *VerifyProofRequest:
		m.unmarshal(data)
	case *VerifyProofResponse:
		m.unmarshal(data)
	case *GenerateProofRequest:
		m.unmarshal(data)
	case *GenerateProofResponse:
		m.unmarshal(data)
	case *HashRequest:
		m.unmarshal(data)
	case *HashResponse:
		m.unmarshal(data)
	case *MerkleProofRequest:
		m.unmarshal(data)
	case *MerkleProofResponse:
		m.unmarshal(data)
	case *HealthRequest:
		m.unmarshal(data)
	case *HealthResponse:
		m.unmarshal(data)
	default:
		return fmt.Errorf("trinityCodec: unsupported message type %T", v)
	}
	return nil
}

// Ensure our codec is registered as the "proto" codec with gRPC. This is done
// once at init time; per-call we also force it via grpc.ForceCodec.
func init() {
	encoding.RegisterCodec(trinityCodec{})
}

// =============================================================================
// ZKComplianceServiceClient — manual gRPC client
// =============================================================================

// ZKComplianceServiceClient is the interface implemented by the gRPC client.
type ZKComplianceServiceClient interface {
	VerifyComplianceProof(ctx context.Context, in *VerifyProofRequest, opts ...grpc.CallOption) (*VerifyProofResponse, error)
	GenerateComplianceProof(ctx context.Context, in *GenerateProofRequest, opts ...grpc.CallOption) (*GenerateProofResponse, error)
	PoseidonHash(ctx context.Context, in *HashRequest, opts ...grpc.CallOption) (*HashResponse, error)
	VerifyMerkleProof(ctx context.Context, in *MerkleProofRequest, opts ...grpc.CallOption) (*MerkleProofResponse, error)
	Health(ctx context.Context, in *HealthRequest, opts ...grpc.CallOption) (*HealthResponse, error)
}

// ZKClient wraps a gRPC ClientConn to the Rust ZK service.
type ZKClient struct {
	conn   *grpc.ClientConn
	target string
}

// NewZKClient dials the ZK compliance service. Dialing is lazy; an unreachable
// endpoint does not cause an error here. Callers should handle per-RPC errors
// gracefully (log + continue) so the pipeline is not blocked by ZK downtime.
func NewZKClient(endpoint string) (*ZKClient, error) {
	if endpoint == "" {
		return nil, fmt.Errorf("zk endpoint is empty")
	}
	conn, err := grpc.NewClient(
		endpoint,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return nil, fmt.Errorf("dial zk service %s: %w", endpoint, err)
	}
	return &ZKClient{conn: conn, target: endpoint}, nil
}

// Target returns the address the client is configured to reach.
func (c *ZKClient) Target() string { return c.target }

// Close releases the underlying gRPC connection.
func (c *ZKClient) Close() error {
	if c == nil || c.conn == nil {
		return nil
	}
	return c.conn.Close()
}

// forceCodec returns the call option that pins our hand-rolled codec to this RPC.
func forceCodec() grpc.CallOption {
	return grpc.ForceCodec(trinityCodec{})
}

const (
	zkService      = "trinity.ZKComplianceService"
	methodVerify   = "/" + zkService + "/VerifyComplianceProof"
	methodGenerate = "/" + zkService + "/GenerateComplianceProof"
	methodPoseidon = "/" + zkService + "/PoseidonHash"
	methodMerkle   = "/" + zkService + "/VerifyMerkleProof"
	methodZKHealth = "/" + zkService + "/Health"
)

// VerifyComplianceProof verifies a ZK compliance proof without revealing
// private data.
func (c *ZKClient) VerifyComplianceProof(ctx context.Context, in *VerifyProofRequest, opts ...grpc.CallOption) (*VerifyProofResponse, error) {
	out := new(VerifyProofResponse)
	err := c.conn.Invoke(ctx, methodVerify, in, out, append([]grpc.CallOption{forceCodec()}, opts...)...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// GenerateComplianceProof generates a ZK proof for private transaction data.
func (c *ZKClient) GenerateComplianceProof(ctx context.Context, in *GenerateProofRequest, opts ...grpc.CallOption) (*GenerateProofResponse, error) {
	out := new(GenerateProofResponse)
	err := c.conn.Invoke(ctx, methodGenerate, in, out, append([]grpc.CallOption{forceCodec()}, opts...)...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// PoseidonHash computes a Poseidon hash for a privacy-preserving audit trail.
func (c *ZKClient) PoseidonHash(ctx context.Context, in *HashRequest, opts ...grpc.CallOption) (*HashResponse, error) {
	out := new(HashResponse)
	err := c.conn.Invoke(ctx, methodPoseidon, in, out, append([]grpc.CallOption{forceCodec()}, opts...)...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// VerifyMerkleProof verifies a Merkle proof for audit-trail integrity.
func (c *ZKClient) VerifyMerkleProof(ctx context.Context, in *MerkleProofRequest, opts ...grpc.CallOption) (*MerkleProofResponse, error) {
	out := new(MerkleProofResponse)
	err := c.conn.Invoke(ctx, methodMerkle, in, out, append([]grpc.CallOption{forceCodec()}, opts...)...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// Health checks the ZK service health.
func (c *ZKClient) Health(ctx context.Context, in *HealthRequest, opts ...grpc.CallOption) (*HealthResponse, error) {
	out := new(HealthResponse)
	err := c.conn.Invoke(ctx, methodZKHealth, in, out, append([]grpc.CallOption{forceCodec()}, opts...)...)
	if err != nil {
		return nil, err
	}
	return out, nil
}

// Ping performs a lightweight health check with a timeout. Returns nil if the
// ZK service is reachable and reports a healthy status.
func (c *ZKClient) Ping(timeout time.Duration) error {
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()
	resp, err := c.Health(ctx, &HealthRequest{})
	if err != nil {
		return err
	}
	if resp.Status != "" && resp.Status != "ok" && resp.Status != "healthy" {
		return fmt.Errorf("zk service unhealthy: %s", resp.Status)
	}
	return nil
}
