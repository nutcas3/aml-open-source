//! Trinity Guard — Rust ZK Service ("The Shield").
//!
//! Real arkworks Groth16 zero-knowledge proofs over Bn254, plus Poseidon
//! hashing and SHA-256 Merkle proof verification for the privacy-preserving
//! audit trail. This crate is consumed two ways:
//!
//! 1. As a PyO3 extension module (`trinity_zk`) for the Python demo / NER
//!    service to call directly — enabled with the `python` Cargo feature.
//! 2. As a Rust library by the `trinity-zk` gRPC binary (`src/main.rs`), which
//!    builds with default features (no Python linkage) so it links cleanly.
//!
//! The core cryptographic API returns `anyhow::Result`; the PyO3 wrappers
//! (gated on `feature = "python"`) are thin adapters that convert errors to
//! `PyResult` and expose the same functionality to Python.

use ark_bn254::{Bn254, Fr};
use ark_ff::{BigInteger, PrimeField};
use ark_groth16::{Groth16, Proof, ProvingKey, VerifyingKey};
use ark_serialize::{CanonicalDeserialize, CanonicalSerialize};
use ark_snark::SNARK;
use ark_std::rand::rngs::StdRng;
use ark_std::rand::SeedableRng;
use std::sync::Arc;
use tracing::{info, instrument};

pub mod circuit;
pub mod merkle;
pub mod poseidon;

pub use circuit::{ComplianceCircuit, CompliancePublicInputs, ComplianceWitness};
pub use merkle::verify_merkle_proof_inner;
pub use poseidon::hash_field as poseidon_hash_field;

/// Real Groth16 proving/verifying key holder over Bn254.
///
/// Constructing this runs the trusted setup (circuit key generation) once and
/// caches the resulting keys. Both keys are `Arc`-shared so the gRPC server and
/// the PyO3 module can serve concurrent prove/verify requests without copying.
pub struct ZKComplianceVerifier {
    proving_key: Arc<ProvingKey<Bn254>>,
    verifying_key: Arc<VerifyingKey<Bn254>>,
}

impl ZKComplianceVerifier {
    /// Run the Groth16 trusted setup for [`ComplianceCircuit`] and return the
    /// proving and verifying keys.
    ///
    /// The setup RNG is seeded deterministically so the demo service produces
    /// reproducible keys across restarts. A production deployment would seed
    /// the toxic-waste RNG from the OS entropy source and discard it after
    /// setup; the Groth16 machinery here is the real arkworks implementation
    /// regardless of the RNG seed source.
    #[instrument(skip_all)]
    pub fn setup_circuit() -> anyhow::Result<(ProvingKey<Bn254>, VerifyingKey<Bn254>)> {
        let circuit = ComplianceCircuit::for_setup();
        let mut rng = StdRng::seed_from_u64(0x7217_1717);
        let (pk, vk) = Groth16::<Bn254>::circuit_specific_setup(circuit, &mut rng)
            .map_err(|e| anyhow::anyhow!("groth16 setup failed: {e}"))?;
        Ok((pk, vk))
    }

    /// Construct a new verifier, running the trusted setup on first call.
    pub fn new() -> anyhow::Result<Self> {
        info!("initializing Rust ZK Compliance Verifier (real arkworks Groth16 / Bn254)");
        let (pk, vk) = Self::setup_circuit()?;
        info!("ZK circuit keys generated successfully");
        Ok(Self {
            proving_key: Arc::new(pk),
            verifying_key: Arc::new(vk),
        })
    }

    /// Verify a compliance proof without revealing private data.
    ///
    /// `proof_bytes`   — canonical arkworks serialization of a `Proof<Bn254>`.
    /// `public_inputs` — canonical serialization of [`CompliancePublicInputs`].
    ///
    /// Returns `true` iff the proof is valid for the given public inputs under
    /// the cached verifying key.
    #[instrument(skip_all, fields(proof_len = proof_bytes.len()))]
    pub fn verify_compliance_proof(
        &self,
        proof_bytes: Vec<u8>,
        public_inputs: Vec<u8>,
    ) -> anyhow::Result<bool> {
        let proof: Proof<Bn254> = Proof::deserialize_uncompressed_unchecked(&proof_bytes[..])
            .map_err(|e| anyhow::anyhow!("invalid proof bytes: {e}"))?;
        let public = CompliancePublicInputs::from_bytes(&public_inputs)
            .map_err(|e| anyhow::anyhow!("invalid public inputs: {e}"))?;

        let inputs = public.to_vec();
        let is_valid = Groth16::<Bn254>::verify(&self.verifying_key, &inputs, &proof)
            .map_err(|e| anyhow::anyhow!("groth16 verify failed: {e}"))?;

        info!(is_valid, "ZK proof verification complete");
        Ok(is_valid)
    }

    /// Generate a ZK proof for private transaction data.
    ///
    /// `private_data` — canonical serialization of [`ComplianceWitness`]
    ///                   (amount || sender_hash, each a little-endian field elem).
    /// `public_data`  — canonical serialization of [`CompliancePublicInputs`].
    ///
    /// Returns the canonical serialization of the generated `Proof<Bn254>`.
    #[instrument(skip_all, fields(private_len = private_data.len()))]
    pub fn generate_compliance_proof(
        &self,
        private_data: Vec<u8>,
        public_data: Vec<u8>,
    ) -> anyhow::Result<Vec<u8>> {
        let (amount, sender_hash) = deserialize_witness(&private_data)
            .map_err(|e| anyhow::anyhow!("invalid private data: {e}"))?;
        let public = CompliancePublicInputs::from_bytes(&public_data)
            .map_err(|e| anyhow::anyhow!("invalid public data: {e}"))?;

        let circuit = ComplianceCircuit::from_witness(
            ComplianceWitness {
                amount,
                sender_hash,
            },
            public,
        );

        // Fresh per-proof randomness from the OS clock so two proofs for the
        // same statement are not identical.
        let seed = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos() as u64)
            .unwrap_or(0x5EED);
        let mut rng = StdRng::seed_from_u64(seed);

        let proof = Groth16::<Bn254>::prove(&self.proving_key, circuit, &mut rng)
            .map_err(|e| anyhow::anyhow!("groth16 prove failed: {e}"))?;

        let mut out = Vec::new();
        proof
            .serialize_uncompressed(&mut out)
            .map_err(|e| anyhow::anyhow!("proof serialize failed: {e}"))?;
        info!(proof_len = out.len(), "ZK proof generated");
        Ok(out)
    }
}

/// Deserialize a witness buffer into (amount, sender_hash). Each field element
/// is encoded as its little-endian big-integer byte representation, concatenated.
pub fn deserialize_witness(
    bytes: &[u8],
) -> Result<(Fr, Fr), ark_serialize::SerializationError> {
    let field_size = (<Fr as PrimeField>::MODULUS_BIT_SIZE as usize).div_ceil(8);
    if bytes.len() != field_size * 2 {
        return Err(ark_serialize::SerializationError::InvalidData);
    }
    let amount = Fr::deserialize_uncompressed_unchecked(&bytes[..field_size])?;
    let sender_hash = Fr::deserialize_uncompressed_unchecked(&bytes[field_size..])?;
    Ok((amount, sender_hash))
}

/// Serialize a witness (amount, sender_hash) into a byte buffer.
pub fn serialize_witness(amount: Fr, sender_hash: Fr) -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(&amount.into_bigint().to_bytes_le());
    out.extend_from_slice(&sender_hash.into_bigint().to_bytes_le());
    out
}

/// Performance benchmark: run `num_operations` Poseidon hashes and report
/// operations per second. Uses real Poseidon, not a mock.
pub fn benchmark_zk_operations(num_operations: usize) -> anyhow::Result<f64> {
    use std::time::Instant;
    if num_operations == 0 {
        anyhow::bail!("num_operations must be > 0");
    }
    info!(num_operations, "benchmarking ZK (Poseidon) operations");
    let start = Instant::now();
    let sample = vec![1u8, 2, 3, 4, 5];
    for _ in 0..num_operations {
        let _ = poseidon::poseidon_hash_core(&sample);
    }
    let duration = start.elapsed();
    let ops_per_sec = num_operations as f64 / duration.as_secs_f64().max(f64::MIN_POSITIVE);
    info!(ops_per_sec, "benchmark complete");
    Ok(ops_per_sec)
}

// =============================================================================
// PyO3 bindings — only compiled when the `python` feature is enabled.
// =============================================================================

#[cfg(feature = "python")]
mod python {
    use super::*;
    use pyo3::exceptions::PyRuntimeError;
    use pyo3::prelude::*;

    /// Python-facing wrapper around the real Groth16 verifier.
    #[pyclass]
    pub struct ZKComplianceVerifier {
        inner: super::ZKComplianceVerifier,
    }

    #[pymethods]
    impl ZKComplianceVerifier {
        /// Construct a new verifier, running the trusted setup on first call.
        #[new]
        fn new() -> PyResult<Self> {
            let inner = super::ZKComplianceVerifier::new()
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
            Ok(Self { inner })
        }

        /// Verify a compliance proof without revealing private data.
        fn verify_compliance_proof(
            &self,
            proof_bytes: Vec<u8>,
            public_inputs: Vec<u8>,
        ) -> PyResult<bool> {
            self.inner
                .verify_compliance_proof(proof_bytes, public_inputs)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))
        }

        /// Generate a ZK proof for private transaction data.
        fn generate_compliance_proof(
            &self,
            private_data: Vec<u8>,
            public_data: Vec<u8>,
        ) -> PyResult<Vec<u8>> {
            self.inner
                .generate_compliance_proof(private_data, public_data)
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))
        }
    }

    /// High-performance Poseidon hashing for privacy-preserving audit trails.
    #[pyfunction]
    fn poseidon_hash(data: Vec<u8>) -> PyResult<Vec<u8>> {
        poseidon::poseidon_hash_core(&data).map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

    /// Verify a Merkle proof for audit-trail integrity.
    #[pyfunction]
    fn verify_merkle_proof(leaf: Vec<u8>, proof: Vec<Vec<u8>>, root: Vec<u8>) -> PyResult<bool> {
        Ok(verify_merkle_proof_inner(&leaf, &proof, &root))
    }

    /// Performance benchmark: operations per second of real Poseidon hashing.
    #[pyfunction]
    fn benchmark_zk_operations(num_operations: usize) -> PyResult<f64> {
        super::benchmark_zk_operations(num_operations)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

    /// Python module definition. Re-exports the real ZK primitives.
    ///
    /// NOTE: the previous version printed a corrupted emoji ("?") on module
    /// load. We use a real shield emoji so the banner renders correctly.
    #[pymodule]
    pub fn trinity_zk(m: &Bound<'_, PyModule>) -> PyResult<()> {
        m.add_class::<ZKComplianceVerifier>()?;
        m.add_function(wrap_pyfunction!(poseidon_hash, m)?)?;
        m.add_function(wrap_pyfunction!(verify_merkle_proof, m)?)?;
        m.add_function(wrap_pyfunction!(benchmark_zk_operations, m)?)?;
        m.add("__version__", env!("CARGO_PKG_VERSION"))?;
        println!("\u{1f6e1} Trinity ZK module loaded \u{2014} real arkworks Groth16 / Bn254");
        println!("\u{26a1} Ready for privacy-preserving cryptographic operations");
        Ok(())
    }
}

// Re-export the PyO3 entry point symbol so `maturin`/`pip` can find it when the
// `python` feature is enabled.
#[cfg(feature = "python")]
pub use python::trinity_zk;

#[cfg(test)]
mod tests {
    use super::*;
    use ark_ff::BigInteger;

    #[test]
    fn test_zk_verifier_creation() {
        // Real trusted setup — this is the heaviest test in the suite (~1-3s).
        let verifier = ZKComplianceVerifier::new().expect("setup must succeed");
        // The keys are real `Arc<ProvingKey<Bn254>>`. `Arc::strong_count == 1`
        // confirms construction succeeded and the keys are owned by the verifier.
        assert_eq!(Arc::strong_count(&verifier.proving_key), 1);
        assert_eq!(Arc::strong_count(&verifier.verifying_key), 1);
    }

    #[test]
    fn test_poseidon_hash_is_deterministic() {
        let data = vec![1, 2, 3, 4, 5];
        let a = poseidon::poseidon_hash_core(&data).expect("hash must succeed");
        let b = poseidon::poseidon_hash_core(&data).expect("hash must succeed");
        assert_eq!(a, b, "poseidon must be deterministic");
        assert!(!a.is_empty(), "poseidon output must be non-empty");
    }

    #[test]
    fn test_merkle_valid_proof() {
        use crate::merkle::hash_leaf;
        let leaf = b"tx-001";
        let sib = hash_leaf(b"tx-002");
        let root = crate::merkle::hash_nodes(&hash_leaf(leaf), &sib);
        assert!(verify_merkle_proof_inner(leaf, &[sib], &root));
    }

    #[test]
    fn test_merkle_invalid_proof() {
        use crate::merkle::hash_leaf;
        let leaf = b"tx-001";
        let sib = hash_leaf(b"tx-002");
        let root = crate::merkle::hash_nodes(&hash_leaf(leaf), &sib);
        // Tamper with the leaf — must fail.
        assert!(!verify_merkle_proof_inner(b"tx-999", &[sib], &root));
    }

    #[test]
    fn test_full_prove_and_verify_roundtrip() {
        // End-to-end: setup -> prove -> verify with a compliant transaction.
        let verifier = ZKComplianceVerifier::new().expect("setup must succeed");

        // amount = 100 (private), threshold = 1000 (public) -> amount < threshold holds.
        let amount = Fr::from(100u64);
        // sender_hash = poseidon(1) (private), sanctions_root = poseidon(2) (public)
        // -> sender_hash != sanctions_root holds.
        let sender_hash = poseidon::hash_field(Fr::from(1u64));
        let sanctions_root = poseidon::hash_field(Fr::from(2u64));
        let threshold = Fr::from(1000u64);

        let witness = serialize_witness(amount, sender_hash);
        let public = CompliancePublicInputs {
            threshold,
            sanctions_root,
        };
        let public_bytes = public.to_bytes();

        let proof = verifier
            .generate_compliance_proof(witness, public_bytes.clone())
            .expect("prove must succeed");
        assert!(!proof.is_empty());

        let is_valid = verifier
            .verify_compliance_proof(proof, public_bytes)
            .expect("verify must succeed");
        assert!(is_valid, "a compliant transaction must verify");
    }

    #[test]
    fn test_non_compliant_amount_fails_to_prove() {
        // amount = 2000 > threshold = 1000 -> the range-check constraint is
        // unsatisfiable. In release mode Groth16::prove uses a debug_assert for
        // satisfaction, so the proof may be produced but will fail verification.
        let verifier = ZKComplianceVerifier::new().expect("setup must succeed");
        let amount = Fr::from(2000u64);
        let sender_hash = poseidon::hash_field(Fr::from(1u64));
        let sanctions_root = poseidon::hash_field(Fr::from(2u64));
        let threshold = Fr::from(1000u64);

        // Sanity: amount > threshold in raw integer form.
        assert!(
            amount.into_bigint() > threshold.into_bigint(),
            "test precondition: amount must exceed threshold"
        );

        let witness = serialize_witness(amount, sender_hash);
        let public = CompliancePublicInputs {
            threshold,
            sanctions_root,
        }
        .to_bytes();

        // The prover may or may not error (debug_assert-gated in release), but
        // the resulting proof MUST fail verification for a non-compliant tx.
        match verifier.generate_compliance_proof(witness, public.clone()) {
            Err(_) => { /* ideal: prover rejects unsatisfiable circuit */ }
            Ok(proof) => {
                let is_valid = verifier
                    .verify_compliance_proof(proof, public)
                    .expect("verify must not error");
                assert!(
                    !is_valid,
                    "a non-compliant transaction's proof must fail verification"
                );
            }
        }
    }
}
