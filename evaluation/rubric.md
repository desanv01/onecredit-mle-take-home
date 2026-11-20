# Relevance rubric

Codex selected the eight anchors from the feed before running either ranker. Each has a single upsert, a usable description, and a distinct business activity. Four descriptions are in English and four are in Malay. This is a purposive sample of the supplied synthetic feed.

Judge each candidate using its original business description and the anchor description. Assign 1 when the candidate provides substantially the same main product, service, or activity. Accept translations, paraphrases, location differences, and small differences in service scope. Assign 0 for a shared industry, customer, location, component, or supply-chain connection without the same core activity. Use the actual activity and ignore names and industry labels. Relevance is binary and independent of language.

The judging pool contains the union of the four top-three results for each anchor, ordered by registration number. Codex judges that pool without reading ranker identities, scores, positions, or view memberships. Preserve the text and judge each anchor–candidate pair once. Enter an integer `relevant` and a short `reason` beside every candidate in the pool before running the scoring command.
