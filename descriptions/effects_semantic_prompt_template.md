# Effects semantic prompt template (v1)

You are given:

1) A project "scenario" with absolute timings.
2) A compact Effects Semantic Catalog that defines allowed effect stacks and which parameters can be overridden.

Your job:
- For each provided Adjustment Layer, choose a `effectStyleId` (styleId) that matches the *meaning* of the moment.
- Output overrides ONLY for the parameters listed in the catalog for that style/effect instance.
- Output REAL keyframes (absolute time in seconds). No procedural instructions. No "compute later".

## INPUT: Effects Semantic Catalog (JSON)
{{EFFECTS_SEMANTIC_CATALOG_JSON}}

## OUTPUT FORMAT (JSON)
Return JSON only. Example:

{
  "effectsLibraryVersion": "0.2",
  "layers": [
    {
      "layerId": "C133:L07:AL_TEXT",
      "effectStyleId": "fx_drop_ultrahardbass_v1",
      "window": { "in": 13.97, "out": 18.43 },
      "effects": [
        {
          "id": "lens_blur",
          "overrides": {
            "iris_scale": {
              "keys": [
                { "time": 13.97, "value": 7, "templateRef": "tpl_ease_in_out_soft" },
                { "time": 14.59, "value": 0, "templateRef": "tpl_ease_in_out_soft" },
                { "time": 17.80, "value": 0, "templateRef": "tpl_ease_in_out_soft" },
                { "time": 18.43, "value": 7, "templateRef": "tpl_ease_in_out_soft" }
              ]
            }
          }
        }
      ]
    }
  ]
}

Rules:
- Only override params listed in the catalog for that style's stack entry.
- If you don't need to animate a param, you can omit it.
- Values may be numbers or arrays (vec2).
- For properties with expressions: you may provide `"expression"` and/or `"keys"`.
