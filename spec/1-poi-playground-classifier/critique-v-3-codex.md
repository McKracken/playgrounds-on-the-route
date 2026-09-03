# Concise critique of spec v3

- **[speculative] Classification precedence is internally under-specified.** The revised rules appear to allow a POI to satisfy both an inclusion rule and an exclusion/negative rule, but do not establish one authoritative precedence order for resolving that collision. Two conforming implementations could therefore assign different final labels to the same POI. The spec should define a single ordered decision procedure (including how explicit tags, inferred evidence, and exclusions interact).

- **[speculative] The output contract and uncertainty rules do not fully agree.** The revisions appear to require a definite classifier result while also describing cases with insufficient or conflicting evidence that should remain unknown/ambiguous. Unless an explicit `unknown` result (and its serialization/consumer behavior) is part of the output schema, those cases cannot be represented without violating one of the requirements.

Other blocking contradictions were identified during the earlier review, but I cannot reproduce them confidently enough from retained context to label them verified without reopening the spec.
