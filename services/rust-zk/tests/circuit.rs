//! Integration test: full Groth16 prove + verify roundtrip on the real
//! ComplianceCircuit over Bn254.

use ark_bn254::Fr;
use ark_relations::r1cs::{ConstraintSynthesizer, ConstraintSystem};
use trinity_zk::circuit::{ComplianceCircuit, CompliancePublicInputs};
use trinity_zk::poseidon;
use trinity_zk::{serialize_witness, ZKComplianceVerifier};

#[test]
fn groth16_prove_and_verify_roundtrip() {
    // A compliant transaction: amount below threshold, sender not sanctioned.
    let verifier = ZKComplianceVerifier::new().expect("trusted setup must succeed");

    let amount = Fr::from(250u64);
    let threshold = Fr::from(10_000u64);
    let sender_hash = poseidon::hash_field(Fr::from(42u64));
    let sanctions_root = poseidon::hash_field(Fr::from(99u64));

    let witness = serialize_witness(amount, sender_hash);
    let public = CompliancePublicInputs {
        threshold,
        sanctions_root,
    };
    let public_bytes = public.to_bytes();

    let proof = verifier
        .generate_compliance_proof(witness, public_bytes.clone())
        .expect("proof generation must succeed for a compliant transaction");
    assert!(!proof.is_empty(), "proof must be non-empty");

    let is_valid = verifier
        .verify_compliance_proof(proof, public_bytes)
        .expect("verification must not error");
    assert!(is_valid, "a valid proof must verify as true");
}

#[test]
fn groth16_rejects_non_compliant_amount() {
    // amount exceeds threshold -> the range-check constraint is unsatisfiable,
    // so proving must fail (the prover cannot satisfy the circuit).
    let verifier = ZKComplianceVerifier::new().expect("trusted setup must succeed");

    let amount = Fr::from(50_000u64);
    let threshold = Fr::from(10_000u64);
    let sender_hash = poseidon::hash_field(Fr::from(42u64));
    let sanctions_root = poseidon::hash_field(Fr::from(99u64));

    let witness = serialize_witness(amount, sender_hash);
    let public = CompliancePublicInputs {
        threshold,
        sanctions_root,
    }
    .to_bytes();

    // In release mode, Groth16::prove may produce a proof even for an
    // unsatisfiable circuit (satisfaction is a debug_assert). The proof must
    // fail verification.
    match verifier.generate_compliance_proof(witness, public.clone()) {
        Err(_) => { /* prover rejected unsatisfiable circuit */ }
        Ok(proof) => {
            let is_valid = verifier
                .verify_compliance_proof(proof, public)
                .expect("verify must not error");
            assert!(
                !is_valid,
                "an over-threshold transaction's proof must fail verification"
            );
        }
    }
}

#[test]
fn groth16_rejects_sanctioned_sender() {
    // sender_hash == sanctions_root -> the non-equality constraint is
    // unsatisfiable (the inverse does not exist), so proving must fail.
    let verifier = ZKComplianceVerifier::new().expect("trusted setup must succeed");

    let amount = Fr::from(250u64);
    let threshold = Fr::from(10_000u64);
    let sanctioned = poseidon::hash_field(Fr::from(7u64));

    let witness = serialize_witness(amount, sanctioned);
    let public = CompliancePublicInputs {
        threshold,
        sanctions_root: sanctioned,
    }
    .to_bytes();

    // In release mode, Groth16::prove may produce a proof even for an
    // unsatisfiable circuit (satisfaction is a debug_assert). The proof must
    // fail verification.
    match verifier.generate_compliance_proof(witness, public.clone()) {
        Err(_) => { /* prover rejected unsatisfiable circuit */ }
        Ok(proof) => {
            let is_valid = verifier
                .verify_compliance_proof(proof, public)
                .expect("verify must not error");
            assert!(
                !is_valid,
                "a sanctioned sender's proof must fail verification"
            );
        }
    }
}

#[test]
fn circuit_structure_is_consistent() {
    // Sanity: the structure-only circuit used for setup must synthesize without
    // error (this is what `circuit_specific_setup` relies on).
    let cs = ConstraintSystem::<Fr>::new_ref();
    ComplianceCircuit::for_setup()
        .generate_constraints(cs.clone())
        .expect("setup circuit must synthesize");
    assert!(
        cs.num_constraints() > 0,
        "circuit must contain real constraints"
    );
}
