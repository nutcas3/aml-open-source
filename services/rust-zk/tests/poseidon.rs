//! Integration test: Poseidon hash determinism and basic properties.

use trinity_zk::poseidon;

#[test]
fn poseidon_is_deterministic() {
    let a = poseidon::poseidon_hash_core(&[1, 2, 3, 4, 5]).unwrap();
    let b = poseidon::poseidon_hash_core(&[1, 2, 3, 4, 5]).unwrap();
    assert_eq!(a, b, "identical inputs must produce identical hashes");
}

#[test]
fn poseidon_output_is_32_bytes() {
    // Bn254 Fr is 254 bits -> 32 bytes in little-endian big-integer encoding.
    let h = poseidon::poseidon_hash_core(b"alice->bob:100").unwrap();
    assert_eq!(h.len(), 32, "poseidon digest must be 32 bytes (Bn254 Fr)");
}

#[test]
fn poseidon_distinguishes_inputs() {
    let a = poseidon::poseidon_hash_core(b"alice").unwrap();
    let b = poseidon::poseidon_hash_core(b"bob").unwrap();
    assert_ne!(a, b, "different inputs must produce different hashes");
}

#[test]
fn poseidon_rejects_empty_input() {
    assert!(poseidon::poseidon_hash_core(&[]).is_err());
}
