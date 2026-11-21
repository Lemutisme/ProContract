# Strategy-only intervention on retained selector requests

`scripts/procontract_policy_splice.py` is a pure standard-library transformer:

```python
variant_bytes, proof = splice(
    original_bytes, expected_original_sha256,
    original_policy_utf8_bytes, replacement_policy_utf8_bytes,
)
```

It verifies the source hash and finds the complete pinned policy prefix in either
native `pro_contract_packet_NNNN` context outputs or legacy
`external_pro_contract_packet_NNNN` message tags. Mixed, duplicate, missing or
out-of-order packet sequences are refused. The remaining joined packet text must
be the complete selector facts JSON with its Contract, subject and offered actions.

The implementation walks JSON syntax to find string token boundaries and maps
decoded Unicode character offsets to their original wire positions, including
UTF-8 and escaped surrogate pairs. It replaces only the policy ranges. It never
serializes the whole request. Identity replacement runs the same locator and
allocation logic and copies each unchanged segment's original escape spelling.

Construction keeps packet count, IDs, tags and non-policy bytes unchanged. Each
non-final strategy span retains its original decoded-character capacity; the
last strategy span absorbs the remaining replacement text. This is **in-place
growth, not native rechunking**. The last fragment may exceed the original
fragment size. It measures this declared policy edit under the retained layout,
not the complete input a fresh native candidate deployment would reconstruct.

The proof records old/new request and policy hashes, decoded and wire ranges,
before/after segment hashes, unchanged gap/tail hashes, and the unchanged facts
hash. Caller qualification must check changed candidates as well as identities:
identity equality alone cannot establish that a different policy touches only
the intended ranges.

The v4 source policies are the frozen November14 files:

- Baseline `9672da72d2f541fd26ed35506f0609eeaeed0ef1caf487db98ea731fec5f484b`,
  3,861 bytes, in the baseline package's `policies/continuation.md`.
- Candidate `00a800dc3f406449449b27bbf8c06edf3ad9bd6052b7ae815b7f61faf9f0af28`,
  4,601 bytes, in `candidate-project/policies/continuation.md`.

Qualification under `procontract-policy-splice-qualification-20261119/` checks
all 120 frozen v3 requests as identities (75 named-context and 45 legacy-tag
forms). The 78 baseline points and 18 candidate points also receive forward and
reverse transformations. Independent v3 policy extraction, semantic field
comparison, raw unchanged-gap checks and the fixed 686d input preflight pass for
all 96 variants. The plain policy delta is ±740 bytes; JSON wire delta is ±742.
Four unit controls cover escape spelling, Unicode boundaries and malformed input.

Use the original point/run identity for analysis. A new request hash is an input
variant, not another statistical unit. Record transformed bytes as a declared
intervention (`reconstructed_payload` plus this proof in the unchanged 686d
interface); original A bytes remain `effective_payload`. Historical reasoning,
tool results, resources, schema and cache key stay as they were. This does not
prove full replay fidelity, a whole-run effect, or that an unchanged sampled
decision implies zero downstream effect. The transformer sends nothing.
