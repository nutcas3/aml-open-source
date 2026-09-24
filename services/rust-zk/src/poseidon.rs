//! Real Poseidon hash wrapper using `ark_crypto_primitives::sponge::poseidon`.
//!
//! Poseidon is a SNARK-friendly hash function. We use the same Bn254 parameter
//! set as the compliance circuit so hashes computed here can be fed directly
//! into on-circuit constraints.
//!
//! The core hashing logic is always available (no PyO3 dependency). The
//! `#[pyfunction]` wrapper is only compiled with the `python` Cargo feature.

use ark_bn254::Fr;
use ark_crypto_primitives::sponge::{
    poseidon::{PoseidonConfig, PoseidonSponge},
    CryptographicSponge, FieldBasedCryptographicSponge,
};
use ark_ff::{BigInteger, PrimeField};

use crate::circuit::default_poseidon_config;

/// Convert an arbitrary byte buffer into a single Bn254 field element by
/// interpreting it as a little-endian big integer and reducing mod the field
/// modulus. Deterministic: identical inputs always yield identical field elems.
fn bytes_to_field(bytes: &[u8]) -> Fr {
    Fr::from_le_bytes_mod_order(bytes)
}

/// Convert a field element back to little-endian bytes.
fn field_to_bytes(f: Fr) -> Vec<u8> {
    f.into_bigint().to_bytes_le()
}

/// Hash a single field element with Poseidon, reusing the circuit's config so
/// native and in-circuit hashes agree.
pub fn hash_field(input: Fr) -> Fr {
    let config: PoseidonConfig<Fr> = default_poseidon_config();
    let mut sponge = PoseidonSponge::<Fr>::new(&config);
    sponge.absorb(&input);
    sponge
        .squeeze_native_field_elements(1)
        .pop()
        .expect("poseidon produced at least one output")
}

/// Core Poseidon hash of an arbitrary byte buffer. Returns the digest as
/// little-endian bytes. Deterministic: identical inputs always produce
/// identical outputs. No PyO3 dependency — usable from the gRPC server and tests.
pub fn poseidon_hash_core(data: &[u8]) -> anyhow::Result<Vec<u8>> {
    if data.is_empty() {
        anyhow::bail!("poseidon_hash: input cannot be empty");
    }
    let input = bytes_to_field(data);
    let digest = hash_field(input);
    Ok(field_to_bytes(digest))
}

// =============================================================================
// PyO3 binding — only compiled with the `python` feature.
// =============================================================================

#[cfg(feature = "python")]
mod python {
    use super::poseidon_hash_core;
    use pyo3::exceptions::PyRuntimeError;
    use pyo3::prelude::*;

    /// High-performance Poseidon hashing for privacy-preserving audit trails.
    ///
    /// Accepts an arbitrary byte buffer, reduces it to a Bn254 field element, and
    /// returns the Poseidon hash as little-endian bytes. Deterministic: identical
    /// inputs always produce identical outputs.
    #[pyfunction]
    pub fn poseidon_hash(data: Vec<u8>) -> PyResult<Vec<u8>> {
        poseidon_hash_core(&data).map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }
}

#[cfg(feature = "python")]
pub use python::poseidon_hash;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hash_is_deterministic() {
        let a = poseidon_hash_core(&[1, 2, 3, 4]).unwrap();
        let b = poseidon_hash_core(&[1, 2, 3, 4]).unwrap();
        assert_eq!(a, b, "same input must produce same hash");
    }

    #[test]
    fn hash_differs_for_different_inputs() {
        let a = poseidon_hash_core(&[1, 2, 3]).unwrap();
        let b = poseidon_hash_core(&[4, 5, 6]).unwrap();
        assert_ne!(a, b, "different inputs must produce different hashes");
    }

    #[test]
    fn rejects_empty_input() {
        assert!(poseidon_hash_core(&[]).is_err());
    }
}
