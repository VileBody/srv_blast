"""F2 «Объект» hook — packaged shape-transition combo for AE.

When the user picks an F2 shape in the bot, the overlay:
  * places the chosen shape transition (rhomb / square / star1 / star2 / elipse)
    on every PRE-DROP cut, anchored so its FX lands on the cut;
  * fires F3 ``hook_light`` (rebuild_light.jsx) on the drop;
  * fires a SEEDED-RANDOM F3 transition on each POST-DROP cut, picked from the
    full F3 transition pool (snap_wipe, minimax, invert_flash, extract_flash,
    flash_on_cuts, layer_shake).

Build-side only; the orchestrator emits ``full_edit_config["f2"]`` and
``project_builder._build_f2_overlay_js`` inlines the JSX. F3 effect scripts are
loaded by path from ``mlcore/hooks/f3_effect/`` — no duplication.
"""
