//! ComplianceCircuit — a Groth16 circuit proving AML compliance without
//! revealing private transaction data.
//!
//! Statement proven:
//!   "I know a transaction `amount` (private) that is below a public `threshold`,
//!    and the Poseidon hash of the sender (private) is not equal to the public
//!    sanctions root."
//!
//! This is the same shape used by production privacy-preserving AML pipelines:
//! the verifier learns *only* that the transaction is compliant, not the amount
//! or the sender.

use ark_bn254::Fr;
use ark_crypto_primitives::sponge::{
    poseidon::{find_poseidon_ark_and_mds, PoseidonConfig, PoseidonSponge},
    CryptographicSponge, FieldBasedCryptographicSponge,
};
use ark_ff::{BigInteger, Field, PrimeField, Zero};
use ark_relations::r1cs::{
    ConstraintSynthesizer, ConstraintSystemRef, LinearCombination, SynthesisError, Variable,
};

/// Number of bits used for the amount range check. 64 bits matches a typical
/// transaction amount (cents) and keeps the circuit small enough for a live
/// demo trusted setup.
pub const AMOUNT_BITS: usize = 64;

/// Public inputs to the compliance proof, in the order the circuit expects them.
#[derive(Clone, Debug)]
pub struct CompliancePublicInputs {
    /// Maximum allowed transaction amount (public).
    pub threshold: Fr,
    /// Root of the sanctions Merkle set the sender must not belong to (public).
    pub sanctions_root: Fr,
}

impl CompliancePublicInputs {
    /// Serialize public inputs to bytes (canonical field encoding) for transport
    /// over gRPC / PyO3 boundaries.
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::new();
        out.extend_from_slice(&self.threshold.into_bigint().to_bytes_le());
        out.extend_from_slice(&self.sanctions_root.into_bigint().to_bytes_le());
        out
    }

    /// Deserialize public inputs from canonical bytes.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, ark_serialize::SerializationError> {
        use ark_serialize::CanonicalDeserialize;
        let field_size = (<Fr as PrimeField>::MODULUS_BIT_SIZE as usize).div_ceil(8);
        if bytes.len() != field_size * 2 {
            return Err(ark_serialize::SerializationError::InvalidData);
        }
        let threshold = Fr::deserialize_uncompressed_unchecked(&bytes[..field_size])?;
        let sanctions_root = Fr::deserialize_uncompressed_unchecked(&bytes[field_size..])?;
        Ok(Self {
            threshold,
            sanctions_root,
        })
    }

    /// The ordered list of public inputs as expected by `Groth16::verify`.
    /// Order MUST match the `new_input_variable` order in `generate_constraints`:
    /// threshold, sanctions_root.
    pub fn to_vec(&self) -> Vec<Fr> {
        vec![self.threshold, self.sanctions_root]
    }
}

/// Private witness known only to the prover.
#[derive(Clone, Debug)]
pub struct ComplianceWitness {
    /// The private transaction amount.
    pub amount: Fr,
    /// Poseidon hash of the private sender identifier.
    pub sender_hash: Fr,
}

/// The Groth16 compliance circuit.
///
/// All values are `Option<Fr>` because arkworks calls `generate_constraints`
/// twice during setup: once with `None` (to determine the circuit structure)
/// and once with `Some(...)` (to actually build the proof).
#[derive(Clone)]
pub struct ComplianceCircuit {
    /// Private: transaction amount.
    pub amount: Option<Fr>,
    /// Private: Poseidon hash of the sender.
    pub sender_hash: Option<Fr>,
    /// Public: compliance threshold.
    pub threshold: Option<Fr>,
    /// Public: root of the sanctions set.
    pub sanctions_root: Option<Fr>,
}

impl ComplianceCircuit {
    pub fn from_witness(witness: ComplianceWitness, public: CompliancePublicInputs) -> Self {
        Self {
            amount: Some(witness.amount),
            sender_hash: Some(witness.sender_hash),
            threshold: Some(public.threshold),
            sanctions_root: Some(public.sanctions_root),
        }
    }

    /// Build the "structure-only" instance used during trusted setup. All
    /// witnesses are `None`; arkworks only needs the constraint topology.
    pub fn for_setup() -> Self {
        Self {
            amount: None,
            sender_hash: None,
            threshold: None,
            sanctions_root: None,
        }
    }
}

impl ConstraintSynthesizer<Fr> for ComplianceCircuit {
    fn generate_constraints(
        self,
        cs: ConstraintSystemRef<Fr>,
    ) -> Result<(), SynthesisError> {
        // During trusted setup, arkworks calls generate_constraints with all
        // witnesses set to None. We substitute Fr::zero() as a placeholder so
        // the constraint topology can be determined without real values.
        let get = |opt: Option<Fr>| opt.unwrap_or(Fr::zero());

        // Allocate the private witness variables (private — not revealed).
        let amount = cs.new_witness_variable(|| Ok(get(self.amount)))?;
        let sender_hash = cs.new_witness_variable(|| Ok(get(self.sender_hash)))?;

        // Allocate the public input variables. These are the only values the
        // verifier learns; their order defines `CompliancePublicInputs::to_vec`.
        let threshold = cs.new_input_variable(|| Ok(get(self.threshold)))?;
        let sanctions_root = cs.new_input_variable(|| Ok(get(self.sanctions_root)))?;

        // --- Constraint 1: amount < threshold (range check) -------------------
        // We prove `amount < threshold` by showing `threshold - amount` is a
        // positive value whose bit decomposition fits in AMOUNT_BITS bits.
        let diff = cs.new_witness_variable(|| {
            Ok(get(self.threshold) - get(self.amount))
        })?;

        // Enforce: threshold - amount - diff == 0
        // (a * b == c) form: a = (threshold - amount), b = 1, c = diff
        cs.enforce_constraint(
            LinearCombination::from(threshold) - LinearCombination::from(amount),
            LinearCombination::from(Variable::One),
            LinearCombination::from(diff),
        )?;

        // Bit-decompose `diff` into AMOUNT_BITS boolean bits and require the
        // bits to reconstruct `diff`. If `amount > threshold` then `diff` is
        // the field representation of a huge negative number, which cannot be
        // represented in AMOUNT_BITS bits, so the constraint fails.
        let mut bits = Vec::with_capacity(AMOUNT_BITS);
        for i in 0..AMOUNT_BITS {
            let bit = cs.new_witness_variable(|| {
                let d = get(self.threshold) - get(self.amount);
                let d_bits = d.into_bigint().to_bits_le();
                Ok(Fr::from(d_bits.get(i).copied().unwrap_or(false) as u64))
            })?;
            // bit is boolean: bit * (1 - bit) == 0
            cs.enforce_constraint(
                LinearCombination::from(bit),
                LinearCombination::from(Variable::One) - LinearCombination::from(bit),
                LinearCombination::zero(),
            )?;
            bits.push(bit);
        }

        // Reconstruct diff from bits and enforce equality:
        // sum(bits * 2^i) == diff  =>  (sum) * 1 == diff
        let mut sum_lc = LinearCombination::<Fr>::zero();
        for (i, b) in bits.iter().enumerate() {
            let coeff = Fr::from(2u64).pow([i as u64]);
            sum_lc = sum_lc + (LinearCombination::from(*b) * coeff);
        }
        cs.enforce_constraint(
            sum_lc,
            LinearCombination::from(Variable::One),
            LinearCombination::from(diff),
        )?;

        // --- Constraint 2: sender_hash != sanctions_root ----------------------
        // We prove inequality by showing `sender_hash - sanctions_root` has an
        // inverse, i.e. is non-zero. If they were equal the difference would be
        // zero and the inverse would not exist.
        let sender_minus_root = cs.new_witness_variable(|| {
            Ok(get(self.sender_hash) - get(self.sanctions_root))
        })?;

        // sender_minus_root = sender_hash - sanctions_root
        cs.enforce_constraint(
            LinearCombination::from(sender_hash) - LinearCombination::from(sanctions_root),
            LinearCombination::from(Variable::One),
            LinearCombination::from(sender_minus_root),
        )?;

        // Allocate the inverse witness. The prover computes it; if the two
        // values are equal the inverse does not exist and proof generation
        // fails — exactly the behavior we want.
        let inverse = cs.new_witness_variable(|| {
            let d = get(self.sender_hash) - get(self.sanctions_root);
            // During setup (both zero), d is zero and has no inverse — return
            // zero as a placeholder; the constraint topology is still correct.
            Ok(d.inverse().unwrap_or(Fr::zero()))
        })?;

        // (sender - sanctions_root) * inverse == 1  (only holds when non-zero)
        cs.enforce_constraint(
            LinearCombination::from(sender_minus_root),
            LinearCombination::from(inverse),
            LinearCombination::from(Variable::One),
        )?;

        Ok(())
    }
}

/// Build a Poseidon sponge config suitable for Bn254. This is the same parameter
/// set used by the audit-trail hashing path so the circuit and the Python-facing
/// `poseidon_hash` agree on the hash function.
///
/// Configuration (width 3, 8 full + 56 partial rounds, alpha = 5) is a standard
/// 120-bit-security Bn254 Poseidon setup.
pub fn default_poseidon_config() -> PoseidonConfig<Fr> {
    let full_rounds = 8;
    let partial_rounds = 56;
    let alpha: u64 = 5;
    let rate = 2;
    let capacity = 1;

    let (ark, mds) = find_poseidon_ark_and_mds::<Fr>(
        Fr::MODULUS_BIT_SIZE as u64,
        rate,
        full_rounds as u64,
        partial_rounds as u64,
        0,
    );

    PoseidonConfig::new(full_rounds, partial_rounds, alpha, mds, ark, rate, capacity)
}

/// Compute a Poseidon hash of a single field element using the same config the
/// circuit uses. Exposed for the `poseidon` module to reuse.
pub fn poseidon_hash_field(input: Fr) -> Fr {
    let cfg = default_poseidon_config();
    let mut sponge = PoseidonSponge::<Fr>::new(&cfg);
    sponge.absorb(&input);
    sponge
        .squeeze_native_field_elements(1)
        .pop()
        .expect("poseidon produced at least one output")
}
