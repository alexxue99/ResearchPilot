"""LLM instructions used by the conjecture investigation pipeline.

Stage instructions are combined with OUTPUT_FIELDS and applicable context instructions
to form the developer message. Payloads and response schemas remain at their call sites.
"""

KATEX_INSTRUCTIONS = r"""
Mathematical expressions must be written in valid LaTeX that is supported by KaTeX.
Return complete LaTeX expressions directly. The interface must not need to infer
operators, accents, subscripts, grouping, or formula boundaries from plain text.

Follow these formatting rules:
- Enclose inline math in \( ... \).
- Enclose display math in $$ ... $$, placing each delimiter on its own line.
- Keep ordinary prose and code identifiers outside math delimiters.
- Put literal parameter names such as n_train, lambda_values, and max_features in Markdown inline-code formatting. When using their mathematical meaning in a formula, use the defined mathematical symbol with correctly grouped indices.
- When describing a formula, use mathematical notation instead of Python-style names: \(n_{\text{train}}\), \(\widehat{a}\), \(\Phi_{\text{train}}\), and \(\bar{E}_{\lambda}(m)\). Write the complete formula inside one pair of math delimiters; do not mix plain-text operators and math fragments.
- Each formula must include its entire left-hand side, relation, right-hand side, and any bounds, accents, or exponents within the same delimiters. Prose may introduce or explain a formula outside those delimiters.
- Never output fragments such as L(beta)=sum_ followed by separately delimited terms, or bar followed by a separately delimited E_lambda. Do not use plain-text formula shorthands such as sqrt(...), sum_i, fhat, a_hat, Phi_train, ||a||, or ^(-1); express them with LaTeX commands and grouping.
- Inside \text{...} or \texttt{...}, escape literal underscores as \_. Use unescaped _ only for mathematical subscripts.
- Group multi-character subscripts and superscripts with braces: x_{\text{test},s,i}, not x_test,s,i.
- Use explicit commands for operators and accents: \sum, \prod, \sqrt{...}, \widehat{f}, \log, and \sin. Do not substitute plain-text shorthand such as sum_i or fhat.
- Use \frac{numerator}{denominator} for fractions when it improves readability.
- Balance braces, math delimiters, and any \left / \right pairs.
- Separate control-word commands from following letters: write \rVert a, never \rVerta. Write norms as \lVert a\rVert_2^2.
- Write complete exponent groups, for example \(A^{-1}\), \(x^{k+1}\), and \(\left(1-\alpha\mu\right)^k\).
- Preserve the intended mathematics; do not invent indices, summation bounds, or assumptions to improve formatting.

Before returning your answer, review every formula-containing string. Confirm that
no mathematical operator, function, index, accent, or exponent has been left in
plain text outside its formula delimiters. Rewrite any mixed expression as one
complete LaTeX expression, then check commands, balanced grouping, index scope,
and literal underscores.
Apply these rules to every formula in algorithm_steps, metrics, assumptions, settings descriptions, and assessment prose. Preserve verbatim source quotations and executable Python syntax.
Keep JSON keys, numeric values, arrays of settings, and executable code in their required formats. Apply mathematical formatting to descriptive strings, not to structural data.

Example of a complete metric formula:
\(E_{s,\lambda}(m)=\frac{1}{n_{\text{test}}}\lVert\Phi_{\text{test}}\widehat{a}_{s,\lambda,m}-y_{\text{test}}\rVert_2^2\)
Use this formatting style while preserving the actual indices and definitions of the experiment.

Additional examples of complete expressions:
- Logistic loss: \(L(\beta)=\sum_{i=1}^{n}\log\!\left(1+\exp\!\left(-y_i x_i^\top\beta\right)\right)+\lambda\lVert\beta\rVert_1\).
- A mean statistic and its maximum: \(\bar{E}_{\lambda}(m)=\frac{1}{S}\sum_{s=1}^{S}E_{s,\lambda}(m)\), then \(\max_{m\in\mathcal{M}}\bar{E}_{\lambda}(m)\).
- A matrix inverse: \(\widehat{a}=\left(\frac{\Phi_{\text{train}}^\top\Phi_{\text{train}}}{n_{\text{train}}}+\lambda I_m\right)^{-1}\frac{\Phi_{\text{train}}^\top y_{\text{train}}}{n_{\text{train}}}\).
These examples demonstrate formatting only. Use the actual definitions, assumptions, and indices of the supplied conjecture; do not copy an example's mathematical content into an unrelated investigation.

If returning JSON, apply JSON escaping separately: a single LaTeX backslash must be encoded as \\ in the JSON string. After JSON decoding, the expression must contain single backslashes.
"""

# Shared context appended to any stage when the relevant sources are available.
ATTACHED_PAPER_CONTEXT = (
    " Attached paper passages are context for the user's conjecture. "
    "Use the passages to understand any user-named method and its assumptions. "
    "For evidence extraction, each excerpt must come verbatim from that source's supplied passages."
)

REVIEWED_PAPER_CONTEXT = (
    " Reviewed papers include grounded findings from the "
    "literature review. Use the findings as evidence where appropriate."
)

# Interpretation and literature review.
INTERPRETATION = (
    "Translate \"statement\" into a precise \"normalized_statement\", preserving its scope, "
    "quantifiers, and comparison. Identify "
    "any method mentioned in the claim using your own mathematical knowledge. "
    "Independently inspect attached paper passages for a "
    "matching method and its definition. When an attached paper defines a method that matches "
    "the user's terminology, prioritize that definition over a match inferred from your own "
    "knowledge; do not merge conflicting methods or silently replace the paper's algorithm. "
    "If the match remains ambiguous, state the competing interpretations and the missing "
    "detail in \"ambiguities\". Put given and explicitly chosen working conditions in \"assumptions\", "
    "distinguishing user/source conditions from conditions you choose. In \"objects\", define the mathematical "
    "entities and named method, including the update equations, sampling rules, "
    "and notation needed to implement it. Do not invent missing method definitions. Determine whether a "
    "finite numerical comparison can test a component of the claim. Set \"experimentable\" to true if so. "
    "A missing implementation detail is an assumption to record, not by itself a reason to mark a "
    "measurable claim unexperimentable. "
    "Record unresolved prerequisites in \"ambiguities\" rather than asserting them as facts. "
) + KATEX_INSTRUCTIONS

AI_GENERAL_REASONING = (
    "Use your own mathematical reasoning to either support or contradict the conjecture in \"normalized_statement\". "
    "For up to two such reasonings, give your reasoning in \"observation\" and its \"relation\" to the claim. "
    "Actively use the web search tool, when available, to find and inspect outside sources "
    "that corroborate your reasoning. You are not restricted to attached or supplied sources. "
    "Prefer original papers, theorem statements, and other primary mathematical sources. "
    "List the exact URLs of corroborating sources you found through the search tool in \"source_urls\". "
    "Use \"source_ids\" only to cite sources already supplied in the investigation context. "
    "Only cite a discovered URL if it appeared in the tool results. If web search is unavailable "
    "or you cannot find a corroborating source, give a precise scholarly query for a follow-up search to find potential corroborating sources. "
    "Give an empty \"query\" when your searches or supplied sources suffice. Do not invent citations or findings. "
    "Use an empty \"argument_searches\" list if you have no concrete lead. "
    "General literature search queries will be requested in a separate step."
) + KATEX_INSTRUCTIONS

ADDITIONAL_LITERATURE_SEARCH = (
    "Write three general scholarly search queries into \"queries\" to search for evidence for or against the user's "
    "conjecture. Cover the claimed result, "
    "its assumptions or limits, and possible counterexamples. Make each query independently "
    "searchable in arXiv and Crossref. Use the recorded general reasoning and its sources "
    "as context to find gaps and additional perspectives. Do not produce new mathematical "
    "observations in this step. Do not invent paper titles, authors, citations, "
    "or source findings."
)

SOURCE_SELECTION = (
    "Select the most relevant sources for reviewing whether or not the conjecture is true. "
    "Use the supplied titles and abstracts, plus attached paper context. Attached sources "
    "and sources recorded during Initial reasoning are automatically included in "
    "\"automatically_selected_source_ids\"; do not select them again. "
    "Prefer sources that define the method, test the claim, establish assumptions, or give "
    "counterexamples. Return only source IDs that appear in \"candidates\", in priority order. "
    "Attached sources remain included. Do not infer findings from a title alone."
)

EVIDENCE_EXTRACTION = (
    "Review every supplied source against the conjecture. Give one concise relevance "
    "assessment per source, even when it contains no usable finding. Extract at most two "
    "findings per source, placing the short verbatim quotation under \"excerpt\" in \"findings\" and the exact "
    "source ID under \"source_id\" in \"reviews\". Classify "
    "support, contradiction, qualification, or related context conservatively. Extract any "
    "relevant theorem whose supplied \"statement\" bears on the conjecture, preserving its "
    "assumptions. Do not supply a proof that is absent from the passages. "
    "Quote only from that source's supplied passages; do not invent text. An abstract cannot "
    "establish a proof. Return an empty \"findings\" array when no passage is relevant to the claim."
)

# Experiment design, scripts, and measured result interpretation.
EXPERIMENT_PLANNING = (
    "Design one small computational test using only the \"normalized_statement\", \"objects\", and "
    "\"assumptions\" from Interpretation. Preserve the specified method, including its update "
    "and sampling rules. Keep the entire experiment within \"max_experiment_seconds\" and "
    "the number of \"seeds\" no greater than \"max_repetitions\" (this limits the count, not seed values). "
    "Specify each detail once: exact settings, varied parameters, "
    "held-constant settings, stopping rule, and work cap belong in \"parameter_ranges_json\"; "
    "seed values belong only in \"seeds\". In \"algorithm_steps\", write concrete, ordered pseudocode "
    "as an array of strings, one operation or logical step per item, not Python code or "
    "a narrative explanation. Specify data generation, method updates or fitting, parameter "
    "sweeps, baseline comparisons, seed loops, and measurement aggregation where applicable. "
    "Use formulas when needed and reference parameter names instead of repeating values. "
    "Define measured outcomes once in \"metrics\", including units, formulas, and aggregation "
    "across \"seeds\" where applicable. Put short baseline labels in \"baseline\", with corresponding baseline pseudocode "
    "in \"algorithm_steps\". \"additional_assumptions\" must contain only assumptions added by this "
    "experiment; Interpretation \"assumptions\" are inherited automatically. "
    "Keep prose brief. Evidence synthesis will independently assess the "
    "executed experiment. If no honest finite test can be specified, use \"test_type\"=not_meaningful."
) + KATEX_INSTRUCTIONS

EXPERIMENT_PLANNING_RECOVERY = (
    EXPERIMENT_PLANNING + " The claim has a measurable component. Find one bounded comparison "
    "that could test it. Use not_meaningful only if no honest test can be specified."
)

EXPERIMENT_PLANNING_REVISION = (
    EXPERIMENT_PLANNING + " Revise the previous design to provide valid JSON parameters, "
    "distinct integer \"seeds\" within the limit, nonempty \"metrics\" and ordered \"algorithm_steps\". "
    "Preserve the intended comparison."
)

EXPERIMENT_CODE = (
    "Implement the supplied experiment design in self-contained Python stdlib code in \"code\". Use the "
    "attached and reviewed paper context to implement the named method; do not silently "
    "substitute a simpler algorithm. Follow the supplied pseudocode given in \"algorithm_steps\". Follow the \"metrics\", exact \"seeds\", "
    "held-constant settings in \"design.parameter_ranges_json\" (or \"design.parameters\" for saved "
    "designs), and fixed stopping rule. Respect \"inherited_assumptions\" and \"additional_assumptions\" "
    "when present, or \"design.assumptions\" in saved designs. The code must write a result.json object with "
    "actual measured outputs and enough raw values to assess the conjecture. Keep its "
    "UTF-8 size at most \"max_result_json_bytes\" and emit finite JSON numbers only. For a paired "
    "comparison, use numeric \"control\" and \"treatment\" arrays and boolean higher_supports. "
    "\"control\" and \"treatment\" may be dictionaries keyed by the same regimes. Set higher_supports "
    "to true when a larger \"treatment\" metric points in the conjectured direction, otherwise "
    "false. Preserve seed pairing and include matching censoring flags where relevant. Also "
    "return a separate \"visualization_code\" Python script that reads result.json from its "
    "current directory and creates visualization.svg in that directory. Choose an "
    "appropriate static visualization for the planned \"metrics\" and result structure, using your "
    "judgment to choose a graph, plot, heatmap, chart, or another suitable form. The visualization's "
    "SVG must obey \"svg_contract\", with no scripts, styles, or external resources. Do not "
    "hardcode plotted measurements in \"visualization_code\"; read them from result.json. "
    "Label relevant axes, units, series, nodes, edges, and color scales honestly where applicable. Each Python script must fit within "
    "\"max_generated_code_chars\" characters, leaving room for added design comments. "
    "Keep execution within \"max_experiment_seconds\". Do not fabricate measurements."
)

EXPERIMENT_CODE_REVISION = (
    "Write complete, syntactically valid, self-contained Python for the supplied design. "
    "Write actual measured values to result.json. Use only the standard library, bounded "
    "loops, the exact \"seeds\" and stopping rule, and the named algorithm where specified. Also "
    "return \"visualization_code\" that reads result.json and creates visualization.svg, using your "
    "judgment to choose an appropriate static visualization (such as a graph, plot, heatmap, or chart) using "
    "standard-library SVG primitives that obey \"svg_contract\". Read measurements from "
    "result.json rather than hardcoding them. Keep each script within \"max_generated_code_chars\", "
    "result.json within \"max_result_json_bytes\", and execution within \"max_experiment_seconds\". "
    "Do not invent outputs."
)

EXPERIMENT_VISUALIZATION = (
    "Write a self-contained Python standard-library script that reads result.json in its "
    "current directory and writes visualization.svg there. Use your judgment to choose a static "
    "visualization appropriate to the actual recorded measurements and the experiment's \"metrics\", "
    "such as a graph, plot, heatmap, chart, or another suitable form. Label relevant axes, series, units, "
    "nodes, edges, color scales, and any censoring honestly where applicable. "
    "Follow the supplied \"svg_contract\" for permitted elements, "
    "attributes, local references, and SVG output size. Use presentation attributes directly. "
    "Do not include scripts, styles, external resources, or embedded result values. "
    "All plotted values must be read from result.json at runtime; supplied \"result_json\" is "
    "context for choosing the visualization and field paths, not values to hardcode into the script. "
    "Use \"previous_code\" and \"revision_reason\" only to correct the prior visualization. "
    "Keep the Python script within \"max_generated_code_chars\" and execution within \"max_experiment_seconds\"."
)

EXPERIMENT_RESULT_INTERPRETATION = (
    "Interpret the experiment results in \"result\" and \"raw_result\" as evidence for or against the conjecture. "
    "Use the \"design\" to understand the experiments. "
    "Put your interpretation in \"finding\", and fill in the \"relation\" between the results and the conjecture, the "
    "\"uncertainty\", \"robustness\", and outcome-specific \"confounders\". "
    "Use supports or contradicts only when the recorded comparisons warrant it; otherwise use inconclusive. Cite the exact result JSON "
    "fields used for your conclusion in \"evidence_paths\" as RFC 6901 pointers into \"raw_result\". "
    "For an uninformative result, use inconclusive and explain why. Do not invent measurements, "
    "claim statistical significance without a test, or treat finite trials as proof."
) + KATEX_INSTRUCTIONS

EXPERIMENT_CODE_RUNTIME_REPAIR = (
    "Repair the supplied Python experiment so it runs and writes measured outputs to "
    "result.json. Preserve the planned algorithm, held-constant parameter settings, \"seeds\", \"metrics\", and stopping "
    "rule. Use only Python's standard library and bounded work. The executor error is "
    "diagnostic, not evidence for the conjecture. Keep the script within \"max_generated_code_chars\", "
    "result.json within \"max_result_json_bytes\", and execution within \"max_experiment_seconds\". "
    "Return a JSON object with only \"code\" containing the complete Python script, not bare Python text."
)

# Synthesis and qualitative assessment.
ASSESSMENT_PRESENTATION = (
    " In user-facing prose, refer to experiments using their supplied \"experiment_labels\" "
    "(number and descriptive name), and papers using the exact supplied \"source_references\" "
    "titles. Internal IDs are for provenance, not prose citations. Never invent a paper title; "
    "Link paper titles using Markdown [article title](supplied URL) when a URL is available; "
    "never use a URL or identifier as the hyperlink's visible text. "
    "If its title is unavailable, say so. "
    "Write short, readable sentences and separate evidence from limitations with paragraph breaks."
) + KATEX_INSTRUCTIONS
EVIDENCE_SYNTHESIS = (
    "Synthesize the supplied evidence about the exact conjecture. Separate direct support, "
    "contradictions, qualified or related findings, and experimental results. Respect each "
    "source's assumptions and whether its text was inspected or only its abstract. Identify "
    "conflicts and missing evidence. Do not turn unspecified settings into a universal assertion; "
    "distinguish support in tested settings from unresolved broader scope. Independently reason from the exact conjecture, "
    "experimental methods, assumptions, and recorded measurements to determine support, "
    "contradictions, uncertainty, confounders, and limits of generalization. Treat prior "
    "result interpretations as provisional; check them against \"experiment_results\" and "
    "the supplied \"planned_experiments\" in \"investigation_context\". Do not adopt a planner's "
    "expected outcomes or assessments. Do not invent results or turn numerical observations "
    "into a proof. General reasoning observations remain unverified leads unless the "
    "supplied extracted findings corroborate them. Return a concise \"summary\", a narrower conjecture only if supported "
    "(otherwise an empty string), and unresolved questions."
) + ASSESSMENT_PRESENTATION

QUALITATIVE_ASSESSMENT = (
    "Assess whether the supplied evidence supports the conjecture. Return a freely worded "
    "qualitative \"judgment\" and an explanatory \"rationale\". Do not assign probabilities, "
    "percentages, numerical confidence scores, or a verdict from a fixed set of categories. "
    "You may conclude, for example, 'Supported in the tested setting; broader generality unresolved', "
    "or use different wording that best expresses your assessment. Explain which specific evidence most affects "
    "the judgment: relevant literature, executed measurements, conflicting findings, and remaining scope limits. "
    "Separate support for the observed phenomenon from uncertainty about its generality. "
    "Do not interpret unspecified distributions or ridge strengths as a universal assertion "
    "unless the conjecture explicitly makes one. State your scope interpretation when it matters. "
    "Missing evidence about generality is not itself contradictory evidence. Reevaluate earlier "
    "unresolved questions against the supplied findings; do not repeat uncertainties already resolved. "
    "Respect source assumptions, distinguish inspected text from abstracts, and cite only supplied "
    "papers and experiments. Do not invent evidence or treat proposed but unrun experiments as results. "
    "If evidence is insufficient, explain what cannot yet be determined and why. "
    "Use as much explanation as needed to make the judgment and its practical scope clear."
) + ASSESSMENT_PRESENTATION

# Canonical field meanings. Paths distinguish nested fields with the same name.
# OUTPUT_FIELDS is generated from these definitions and appended to every stage prompt.
FIELD_INSTRUCTIONS = {
    "interpretation": {
        "normalized_statement": "String: restate the exact claim precisely, preserving its scope, quantifiers, and comparison; do not assess its truth.",
        "mathematical_domain": "String: identify the mathematical subject area relevant to the claim.",
        "objects": "String array: define entities, notation, and named methods, including update equations and sampling rules needed for implementation; make these definitions self-contained.",
        "assumptions": "String array: record given conditions and explicitly chosen working conditions; distinguish user/source conditions from model-chosen conditions without repeating an assumed label on every entry.",
        "measurable_predictions": "String array: describe observable quantities or trends implied by the claim, not measured results.",
        "ambiguities": "String array: list unresolved questions about meaning, scope, method definitions, and missing prerequisites; use [] when none remain.",
        "experimentable": "Boolean: true if a finite numerical test can examine at least one component; false if no component can meaningfully be tested numerically.",
    },
    "ai_general_reasoning": {
        "argument_searches": "Object array: return at most two concrete mathematical leads, or [] if none are available; each item must contain all fields below.",
        "argument_searches[].relation": "String: supports or contradicts describes the lead's hypothesized direction relative to the exact claim, not a verified finding.",
        "argument_searches[].observation": "String: explain the concrete mathematical reasoning behind this unverified lead.",
        "argument_searches[].query": "String: give a precise scholarly follow-up search query when corroboration is missing; use an empty string when searches or supplied sources suffice.",
        "argument_searches[].source_ids": "String array: cite only exact IDs of already supplied \"investigation_context.literature_sources\"; use [] if no supplied source corroborates this lead.",
        "argument_searches[].source_urls": "String array: give exact corroborating source URLs from the current web-search tool results; use [] when none were found or web search is unavailable.",
    },
    "additional_literature_search": {
        "queries": "String array: provide exactly three distinct nonempty scholarly queries, each at most 300 characters, covering the claimed result, assumptions or limits, and counterexamples; make each usable in arXiv and Crossref.",
    },
    "source_selection": {
        "selected_source_ids": "String array: copy distinct IDs from \"candidates\" in review priority order, at most \"capacity\" entries; exclude \"automatically_selected_source_ids\" and use [] when no additional candidate is relevant.",
    },
    "evidence_extraction": {
        "reviews": "Object array: provide exactly one relevance review for every entry in \"sources\", including entries with no usable findings.",
        "reviews[].source_id": "String: copy the exact \"sources[].source_id\" for the source being reviewed; do not use a title, URL, or invented ID.",
        "reviews[].relevance": "String: high for a direct method or claim match, medium for partial applicability, low for tangential material, or unknown when source text is unavailable.",
        "reviews[].summary": "String: concisely describe what the supplied source text says about the method or claim; distinguish reviewed passages from abstracts and do not infer unseen findings.",
        "reviews[].limitations": "String array: identify gaps in the supplied text and limits of its applicability; use [] when none are identified. If \"passages\" is empty, explain the unavailable text here.",
        "findings": "Object array: provide at most two grounded findings per source; use [] if no supplied passage is relevant, and no findings for sources with empty \"passages\".",
        "findings[].source_id": "String: copy the exact \"sources[].source_id\" of the source containing this quotation.",
        "findings[].excerpt": "String: copy a short quotation verbatim from that source's \"sources[].passages[].text\"; do not paraphrase, quote another source, or supply unseen text.",
        "findings[].evidence_type": "String: theorem for a stated mathematical result, proof for an actual proof passage, empirical_result for measured findings, counterexample for a violating example, discussion for explanatory analysis, or related_result for a relevant result that does not directly test the claim. Classify the quotation, not the whole paper; an abstract does not establish proof.",
        "findings[].relation": "String: supports favors the claim under matching assumptions, contradicts opposes it under matching assumptions, qualifies restricts its scope, or neutral provides relevant context without deciding it.",
        "findings[].assumptions": "String array: record conditions under which the quoted finding applies; do not substitute conditions chosen for a future experiment.",
        "findings[].notes": "String: explain the quotation's applicability to the exact claim and any assumption mismatch or abstract-only text scope.",
    },
    "experiment_planning": {
        "name": "String: give a short descriptive label identifying the experiment or comparison, not an internal ID or an assessment of the claim.",
        "baselines": "String array: name the comparison methods or conditions with short labels; implement them in \"algorithm_steps\" rather than explaining them here; use [] when none apply.",
        "metrics": "String array: define the measured quantities, formulas, units, and aggregation across \"seeds\" where applicable; do not predict their values or interpret future outcomes.",
        "seeds": "Integer array: choose distinct random \"seeds\", no more than \"max_repetitions\" entries; the limit applies to count, not integer magnitude. Use [] for a deterministic test.",
        "algorithm_steps": "String array: write concrete ordered pseudocode, one operation or logical step per item, including data generation, sampling, method updates or fitting, sweeps, baseline comparisons, seed loops, and measurement aggregation where applicable. Reference parameter names; do not return Python code or a narrative explanation.",
        "parameter_ranges_json": "String: encode one valid JSON object containing exact settings, varied parameters and sweep values, held-constant settings, stopping rule, and work cap. Do not return an object directly, repeat seed values, or use unspecified placeholders.",
        "additional_assumptions": "String array: record only conditions newly adopted for this experiment; Interpretation \"assumptions\" are inherited automatically. Use [] when no extra assumptions are needed.",
        "test_type": "String: falsifying if this finite comparison can challenge a component of the claim, illustrative if it only demonstrates behavior without testing that component, or not_meaningful if no honest finite test can be specified.",
    },
    "experiment_code": {
        "code": "String: return the complete self-contained Python experiment script implementing \"design.algorithm_steps\", \"metrics\", \"seeds\", and exact settings from \"design.parameter_ranges_json\" or saved \"design.parameters\". Write actual finite measurements to result.json; obey the supplied character, output-size, and execution limits.",
        "visualization_code": "String: return a separate complete Python script that reads result.json at runtime and writes visualization.svg obeying \"svg_contract\". Use your judgment to choose an appropriate static visualization for the planned \"metrics\" and result structure, such as a graph, plot, heatmap, or chart; do not hardcode measured values or run the experiment again.",
    },
    "experiment_visualization": {
        "visualization_code": "String: return the complete replacement Python visualization script that reads result.json and writes visualization.svg obeying \"svg_contract\". Use supplied \"result_json\" only to understand field paths and choose an appropriate static visualization using your judgment; use \"previous_code\" and \"revision_reason\" to fix the prior visualization; never hardcode supplied measurements.",
    },
    "experiment_code_repair": {
        "code": "String: return the complete repaired experiment Python script, using \"previous_code\" and executor diagnostics while preserving the method, \"seeds\", \"metrics\", exact settings, and stopping rule. Write actual measurements to result.json. Return no \"visualization_code\" in this stage.",
    },
    "experiment_execution": {
        "finding": "String: describe the observed contrast for the exact conjecture, citing only measurements verifiable in \"raw_result\"; do not invent a comparison absent from the output.",
        "uncertainty": "String: explain sampling, censoring, or numerical uncertainty and what cannot be concluded; do not invent statistical significance or uncomputed intervals.",
        "robustness": "String: describe which \"seeds\" and parameter regimes were actually checked and whether the contrast persisted; explicitly state when no robustness checks were performed.",
        "relation": "String: supports or contradicts only when the measured comparison warrants that conclusion for the exact claim; otherwise use inconclusive.",
        "confounders": "String array: identify possible alternative explanations justified by the method or output; use [] when none are identified.",
        "evidence_paths": "String array: supply one to ten RFC 6901 JSON pointers resolving to existing \"raw_result\" values that substantiate finding, for example /control/0. Do not prefix /\"raw_result\" or cite fields that exist only in the normalized result.",
    },
    "evidence_synthesis": {
        "summary": "String: independently integrate the grounded literature, experiment methods, assumptions, and recorded measurements. Distinguish direct support, contradictions, qualified or related findings, uncertainty, and scope; treat earlier result interpretations as provisional and check them against \"experiment_results\".",
        "revised_conjecture": "String: supply a narrower version of the original claim only when the evidence warrants that revision; otherwise return an empty string, not a speculative replacement or a copy of the original.",
        "unresolved_questions": "String array: identify specific missing evidence, assumptions to verify, and remaining review or experiment limitations from your own analysis; do not copy planner assessments. Use [] only when no unresolved issues are identified.",
    },
    "confidence_estimation": {
        "judgment": "Nonempty string: freely phrase your qualitative conclusion about the conjecture, distinguishing support in tested or established settings from unresolved broader scope. No numeric score or fixed verdict category.",
        "rationale": "Nonempty string: explain the evidence driving your judgment, its scope, any contradictions, and what remains unresolved. Use enough prose to distinguish supporting findings from limitations on generality.",
    },
}

OUTPUT_RULES = {
    "experiment_planning": (
        "For not_meaningful, keep \"name\" as a short label, use empty arrays for inapplicable "
        "fields and \"parameter_ranges_json\"=\"{}\". Otherwise \"name\", \"metrics\", and \"algorithm_steps\" "
        "must be nonempty. Do not return \"purpose\", \"decision_criteria\", or \"limitations\"."
    ),
    "experiment_code": "Script strings contain Python without Markdown fences; return the JSON object rather than bare Python text.",
    "experiment_visualization": "Return only the JSON field \"visualization_code\"; its Python text must not contain Markdown fences.",
    "experiment_code_repair": "Return only the JSON field \"code\"; its Python text must not contain Markdown fences.",
}

OUTPUT_FIELDS = {
    stage: "Required output fields (return a JSON object with exactly the requested fields):\n"
           + "\n".join(f'- "{path}": {meaning}' for path, meaning in fields.items())
           + ("\n" + OUTPUT_RULES[stage] if stage in OUTPUT_RULES else "")
    for stage, fields in FIELD_INSTRUCTIONS.items()
}


def required_field_paths(schema: dict, prefix: str = ""):
    """Enumerate required paths, including fields nested in object arrays."""
    for field in schema.get("required", []):
        path = prefix + field
        yield path
        child = schema.get("properties", {}).get(field, {})
        if child.get("type") == "object":
            yield from required_field_paths(child, path + ".")
        elif child.get("type") == "array":
            yield from required_field_paths(child.get("items", {}), path + "[].")


def output_instructions(stage: str, schema: dict) -> str:
    """Reject schema drift rather than leave a required field unexplained."""
    definitions = FIELD_INSTRUCTIONS[stage]
    missing = [path for path in required_field_paths(schema) if not definitions.get(path, "").strip()]
    if missing:
        raise ValueError(f"Missing output field instructions for {stage}: {', '.join(missing)}")
    return OUTPUT_FIELDS[stage]
