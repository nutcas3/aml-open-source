//! Integration test: SHA-256 Merkle proof verification (valid + invalid).

use trinity_zk::merkle::{hash_leaf, hash_nodes, verify_merkle_proof_inner};

fn build_right_leaning_proof(leaf: &[u8], siblings: &[Vec<u8>]) -> (Vec<Vec<u8>>, Vec<u8>) {
    let mut current = hash_leaf(leaf);
    let mut proof: Vec<Vec<u8>> = Vec::new();
    for sib in siblings {
        proof.push(sib.clone());
        current = hash_nodes(&current, sib);
    }
    (proof, current)
}

#[test]
fn valid_merkle_proof_passes() {
    let leaf = b"tx-001:alice->bob:100";
    let sib1 = hash_leaf(b"tx-002:carol->dave:200");
    let sib2 = hash_nodes(&hash_leaf(b"tx-003"), &hash_leaf(b"tx-004"));
    let (proof, root) = build_right_leaning_proof(leaf, &[sib1, sib2]);

    assert!(
        verify_merkle_proof_inner(leaf, &proof, &root),
        "a correctly built proof must verify"
    );
}

#[test]
fn invalid_root_fails() {
    let leaf = b"tx-001:alice->bob:100";
    let sib1 = hash_leaf(b"tx-002:carol->dave:200");
    let (proof, _root) = build_right_leaning_proof(leaf, &[sib1]);

    let bad_root = vec![0u8; 32];
    assert!(
        !verify_merkle_proof_inner(leaf, &proof, &bad_root),
        "a proof against the wrong root must fail"
    );
}

#[test]
fn tampered_leaf_fails() {
    let leaf = b"tx-001:alice->bob:100";
    let sib1 = hash_leaf(b"tx-002:carol->dave:200");
    let (proof, root) = build_right_leaning_proof(leaf, &[sib1]);

    assert!(
        !verify_merkle_proof_inner(b"tampered-leaf", &proof, &root),
        "a proof for the wrong leaf must fail"
    );
}

#[test]
fn tampered_proof_sibling_fails() {
    let leaf = b"tx-001:alice->bob:100";
    let sib1 = hash_leaf(b"tx-002:carol->dave:200");
    let (mut proof, root) = build_right_leaning_proof(leaf, &[sib1]);
    // Flip a bit in the sibling hash.
    proof[0][0] ^= 0xff;

    assert!(
        !verify_merkle_proof_inner(leaf, &proof, &root),
        "a proof with a tampered sibling must fail"
    );
}

#[test]
fn single_leaf_tree_with_empty_proof() {
    let leaf = b"only-tx";
    let root = hash_leaf(leaf);
    assert!(
        verify_merkle_proof_inner(leaf, &[], &root),
        "single-leaf tree with empty proof must verify"
    );
}
