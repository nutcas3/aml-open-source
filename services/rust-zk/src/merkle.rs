//! Real Merkle tree proof verification using SHA-256.
//!
//! The audit trail hashes every transaction into a Merkle tree so that a
//! regulator can verify a transaction was recorded without revealing the full
//! dataset. This module implements proof *verification* (the prover/tree
//! builder lives in the Go backend, which holds the canonical ledger).
//!
//! The core verification logic is always available (no PyO3 dependency). The
//! `#[pyfunction]` wrapper is only compiled with the `python` Cargo feature.

use sha2::{Digest, Sha256};

/// Domain separator for leaf nodes, following the RFC 6962 convention so that
/// leaf hashes and internal node hashes cannot be swapped.
const LEAF_PREFIX: u8 = 0x00;
const NODE_PREFIX: u8 = 0x01;

/// Hash a leaf value: H(0x00 || leaf).
pub fn hash_leaf(leaf: &[u8]) -> Vec<u8> {
    let mut hasher = Sha256::new();
    hasher.update([LEAF_PREFIX]);
    hasher.update(leaf);
    hasher.finalize().to_vec()
}

/// Hash two child nodes into a parent: H(0x01 || left || right).
pub fn hash_nodes(left: &[u8], right: &[u8]) -> Vec<u8> {
    let mut hasher = Sha256::new();
    hasher.update([NODE_PREFIX]);
    hasher.update(left);
    hasher.update(right);
    hasher.finalize().to_vec()
}

/// Verify a Merkle inclusion proof.
///
/// `leaf`  — the raw leaf value (not pre-hashed).
/// `proof` — ordered list of sibling hashes, from the leaf level up to the root.
/// `root`  — the expected Merkle root.
///
/// Each sibling is combined with the running hash. We follow the convention that
/// a sibling on the left is prepended and a sibling on the right is appended.
/// Because we only have the sibling hashes (not left/right flags), we use the
/// standard "sibling is always on the right" convention used by the Go backend's
/// tree builder. The Go side must build proofs with the same convention.
pub fn verify_merkle_proof_inner(leaf: &[u8], proof: &[Vec<u8>], root: &[u8]) -> bool {
    let mut current = hash_leaf(leaf);
    for sibling in proof {
        current = hash_nodes(&current, sibling);
    }
    // Constant-time comparison of the computed root against the expected root.
    constant_time_eq(&current, root)
}

/// Constant-time byte comparison to avoid timing side channels on the root.
fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    if a.len() != b.len() {
        return false;
    }
    let mut diff: u8 = 0;
    for (x, y) in a.iter().zip(b.iter()) {
        diff |= x ^ y;
    }
    diff == 0
}

// =============================================================================
// PyO3 binding — only compiled with the `python` feature.
// =============================================================================

#[cfg(feature = "python")]
mod python {
    use super::verify_merkle_proof_inner;
    use pyo3::prelude::*;

    /// Verify a Merkle proof for audit-trail integrity.
    ///
    /// Returns `true` if `leaf` is included in the tree with the given `root`
    /// according to `proof`, `false` otherwise. This is the Python-facing entry
    /// point used by the demo and audit tooling.
    #[pyfunction]
    pub fn verify_merkle_proof(
        leaf: Vec<u8>,
        proof: Vec<Vec<u8>>,
        root: Vec<u8>,
    ) -> pyo3::PyResult<bool> {
        Ok(verify_merkle_proof_inner(&leaf, &proof, &root))
    }
}

#[cfg(feature = "python")]
pub use python::verify_merkle_proof;

#[cfg(test)]
mod tests {
    use super::*;

    fn build_proof(leaf: &[u8], siblings: &[&[u8]]) -> (Vec<Vec<u8>>, Vec<u8>) {
        // Build a right-leaning tree: each level hashes (current, sibling).
        let mut current = hash_leaf(leaf);
        let mut proof: Vec<Vec<u8>> = Vec::new();
        for sib in siblings {
            proof.push(sib.to_vec());
            current = hash_nodes(&current, sib);
        }
        (proof, current)
    }

    #[test]
    fn valid_proof_passes() {
        let leaf = b"tx-001:alice->bob:100";
        let sib1 = hash_leaf(b"tx-002:carol->dave:200");
        let sib2 = hash_nodes(&hash_leaf(b"tx-003"), &hash_leaf(b"tx-004"));
        let (proof, root) = build_proof(leaf, &[&sib1, &sib2]);

        assert!(
            verify_merkle_proof_inner(leaf, &proof, &root),
            "a correctly built proof must verify"
        );
    }

    #[test]
    fn invalid_proof_fails() {
        let leaf = b"tx-001:alice->bob:100";
        let sib1 = hash_leaf(b"tx-002:carol->dave:200");
        let sib2 = hash_nodes(&hash_leaf(b"tx-003"), &hash_leaf(b"tx-004"));
        let (proof, _root) = build_proof(leaf, &[&sib1, &sib2]);

        // A wrong root must fail verification.
        let bad_root = vec![0u8; 32];
        assert!(
            !verify_merkle_proof_inner(leaf, &proof, &bad_root),
            "a proof against the wrong root must fail"
        );
    }

    #[test]
    fn wrong_leaf_fails() {
        let leaf = b"tx-001:alice->bob:100";
        let sib1 = hash_leaf(b"tx-002:carol->dave:200");
        let (proof, root) = build_proof(leaf, &[&sib1]);

        assert!(
            !verify_merkle_proof_inner(b"tampered-leaf", &proof, &root),
            "a proof for the wrong leaf must fail"
        );
    }

    #[test]
    fn empty_proof_single_node_tree() {
        // A tree with a single leaf has root == hash_leaf(leaf) and an empty proof.
        let leaf = b"only-tx";
        let root = hash_leaf(leaf);
        assert!(
            verify_merkle_proof_inner(leaf, &[], &root),
            "single-leaf tree with empty proof must verify"
        );
    }
}
