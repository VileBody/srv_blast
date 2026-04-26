/*
 * apply_adjustment_effects_cold.jsx
 * Saturation: 0.6 (cold variant)
 *
 * Recreates adjustment layers with effects from the parsed AE project.
 * Run inside After Effects: File > Scripts > Run Script File...
 *
 * Stack order (bottom to top):
 *   Adjustment Layer 1  — Looks (MB LookSuite3)
 *   Adjustment Layer 3  — S_Glow (Sapphire)
 *   Adjustment Layer 2  — Sharpen, Unsharp Mask, Exposure, Curves, Brightness & Contrast, Looks, Mojo II
 *   Adjustment Layer 4  — S_HueSatBright (Sapphire), Curves
 *
 * REQUIREMENTS:
 *   - Magic Bullet Looks (Red Giant / Maxon)
 *   - Magic Bullet Mojo II (Red Giant / Maxon)
 *   - Sapphire (Boris FX) — S_Glow, S_HueSatBright
 *
 * LIMITATIONS:
 *   - Curves (ADBE CurvesCustom) effect is added but its curve data cannot
 *     be set via scripting (CUSTOM_VALUE type). You must adjust curves manually.
 *   - Magic Bullet Looks preset/look data is also CUSTOM_VALUE — the effect
 *     is applied with Strength set, but the look itself must be chosen manually.
 */

(function () {
    // Headless-safe logger. When this sidecar is eval'd from project_template.j2,
    // `logLine` is available in the enclosing scope; fall back to $.writeln.
    // IMPORTANT: we must NEVER call the AE built-in `alert(...)` from a sidecar —
    // on a render node it opens a modal dialog and hangs AE indefinitely
    // (symptom: "Timeout waiting for ae_status.txt").
    function __sidecarLog(msg) {
        try {
            if (typeof logLine === "function") { logLine("INFO", "[sidecar/cold] " + String(msg)); return; }
        } catch (e) {}
        try { $.writeln("[sidecar/cold] " + String(msg)); } catch (e2) {}
    }

    // Resolve target comp:
    //   1) Prefer MAIN_COMP from enclosing scope (injected by project_template.j2).
    //      Required on headless render nodes where openInViewer() does NOT
    //      promote a comp to app.project.activeItem.
    //   2) Fallback to app.project.activeItem (manual run inside AE GUI).
    var comp = null;
    try {
        if (typeof MAIN_COMP !== "undefined" && MAIN_COMP && (MAIN_COMP instanceof CompItem)) {
            comp = MAIN_COMP;
        }
    } catch (eMC) {}
    if (!comp) {
        try {
            if (app.project && app.project.activeItem && (app.project.activeItem instanceof CompItem)) {
                comp = app.project.activeItem;
            }
        } catch (eAI) {}
    }
    if (!comp) {
        __sidecarLog("no target comp (MAIN_COMP undefined, no active CompItem) — skipping sidecar");
        return;
    }
    __sidecarLog("target comp resolved name=\"" + comp.name + "\" w=" + comp.width + " h=" + comp.height + " dur=" + comp.duration.toFixed(2) + "s");

    app.beginUndoGroup("Apply Adjustment Layer Effects");

    try {
        // --- Helper: set effect property by matchName ---
        function setEffectProp(effect, matchName, value) {
            try {
                var prop = effect.property(matchName);
                if (prop && prop.propertyValueType !== PropertyValueType.NO_VALUE &&
                    prop.propertyValueType !== PropertyValueType.CUSTOM_VALUE) {
                    if (prop.numKeys > 0) {
                        while (prop.numKeys > 0) prop.removeKey(1);
                    }
                    prop.setValue(value);
                }
            } catch (e) {
                // Skip unwritable properties silently
            }
        }

        // --- Helper: add adjustment layer spanning comp duration ---
        function addAdjustmentLayer(name) {
            var layer = comp.layers.addSolid(
                [1, 1, 1],       // color (white, doesn't matter for adj)
                name,
                comp.width,
                comp.height,
                comp.pixelAspect,
                comp.duration
            );
            layer.adjustmentLayer = true;
            layer.startTime = 0;
            layer.inPoint = 0;
            layer.outPoint = comp.duration;
            return layer;
        }

        // ==================================================================
        //  ADJUSTMENT LAYER 1 — Looks (bottom of the stack)
        // ==================================================================
        var al1 = addAdjustmentLayer("Adjustment Layer 1 — Looks");

        (function () {
            var fx;
            try {
                fx = al1.Effects.addProperty("MB LookSuite3");
            } catch (e) {
                __sidecarLog("MB LookSuite3 plugin missing — skipping Looks on AL1: " + e.toString());
                return;
            }
            setEffectProp(fx, "MB LookSuite3-0013", 80);  // Strength
        })();

        // ==================================================================
        //  ADJUSTMENT LAYER 3 — S_Glow
        // ==================================================================
        var al3 = addAdjustmentLayer("Adjustment Layer 3 — S_Glow");

        (function () {
            var fx;
            try {
                fx = al3.Effects.addProperty("S_Glow");
            } catch (e) {
                __sidecarLog("Sapphire S_Glow plugin missing — skipping Glow: " + e.toString());
                return;
            }
            setEffectProp(fx, "S_Glow-0050", 2.5);     // Brightness
            setEffectProp(fx, "S_Glow-0052", 0.5);     // Threshold
            setEffectProp(fx, "S_Glow-0054", 250);      // Glow Width
            setEffectProp(fx, "S_Glow-0055", 1);        // Width X
            setEffectProp(fx, "S_Glow-0056", 1);        // Width Y
            setEffectProp(fx, "S_Glow-0057", 1);        // Width Red
            setEffectProp(fx, "S_Glow-0058", 1.19999694824219);  // Width Green
            setEffectProp(fx, "S_Glow-0059", 1.39999389648438);  // Width Blue
            setEffectProp(fx, "S_Glow-0060", 1);        // Subpixel
            setEffectProp(fx, "S_Glow-0100", 1);        // Show
            setEffectProp(fx, "S_Glow-0101", 3);        // Combine
            setEffectProp(fx, "S_Glow-0102", 2);        // Edge Mode
            setEffectProp(fx, "S_Glow-0061", 1);        // Affect Alpha
            setEffectProp(fx, "S_Glow-0065", 1);        // Source Opacity
            setEffectProp(fx, "S_Glow-0066", 1);        // Bg Brightness
            setEffectProp(fx, "S_Glow-0103", 0);        // Atmosphere Amp
            setEffectProp(fx, "S_Glow-0104", 1);        // Atmosphere Freq
            setEffectProp(fx, "S_Glow-0105", 0.59999084472656);  // Atmosphere Detail
            setEffectProp(fx, "S_Glow-0106", 0.12298583984375);  // Atmosphere Seed
            setEffectProp(fx, "S_Glow-0107", 1);        // Atmosphere Speed
            setEffectProp(fx, "S_Glow-0069", 2);        // Opacity
            // Color (RGBA array)
            try {
                var colorProp = fx.property("S_Glow-0051");
                if (colorProp) colorProp.setValue([1, 1, 1, 1]);
            } catch (e) {}
            // Threshold Add Color
            try {
                var tacProp = fx.property("S_Glow-0053");
                if (tacProp) tacProp.setValue([0, 0, 0, 1]);
            } catch (e) {}
        })();

        // ==================================================================
        //  ADJUSTMENT LAYER 2 — Sharpen, Unsharp Mask, Exposure, Curves,
        //                        Brightness & Contrast, Looks, Mojo II
        // ==================================================================
        var al2 = addAdjustmentLayer("Adjustment Layer 2 — Color & Sharpen");

        // 1. Sharpen
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("ADBE Sharpen"); } catch (e) { return; }
            setEffectProp(fx, "ADBE Sharpen-0001", 20);  // Sharpen Amount
        })();

        // 2. Unsharp Mask
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("ADBE Unsharp Mask2"); } catch (e) { return; }
            setEffectProp(fx, "ADBE Unsharp Mask2-0004", 0);   // Color Mode
            setEffectProp(fx, "ADBE Unsharp Mask2-0001", 50);  // Amount
            setEffectProp(fx, "ADBE Unsharp Mask2-0002", 1);   // Radius
            setEffectProp(fx, "ADBE Unsharp Mask2-0003", 0);   // Threshold
        })();

        // 3. Exposure
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("ADBE Exposure2"); } catch (e) { return; }
            setEffectProp(fx, "ADBE Exposure2-0001", 1);     // Channel: Master
            setEffectProp(fx, "ADBE Exposure2-0003", -0.3);  // Exposure
            setEffectProp(fx, "ADBE Exposure2-0004", 0);     // Offset
            setEffectProp(fx, "ADBE Exposure2-0005", 1);     // Gamma Correction
            setEffectProp(fx, "ADBE Exposure2-0008", 0);     // Red Exposure
            setEffectProp(fx, "ADBE Exposure2-0009", 0);     // Red Offset
            setEffectProp(fx, "ADBE Exposure2-0010", 1);     // Red Gamma
            setEffectProp(fx, "ADBE Exposure2-0013", 0);     // Green Exposure
            setEffectProp(fx, "ADBE Exposure2-0014", 0);     // Green Offset
            setEffectProp(fx, "ADBE Exposure2-0015", 1);     // Green Gamma
            setEffectProp(fx, "ADBE Exposure2-0018", 0);     // Blue Exposure
            setEffectProp(fx, "ADBE Exposure2-0019", 0);     // Blue Offset
            setEffectProp(fx, "ADBE Exposure2-0020", 1);     // Blue Gamma
            setEffectProp(fx, "ADBE Exposure2-0022", 0);     // Bypass Linear Light
        })();

        // 4. Curves (curve data must be set manually!)
        (function () {
            try { al2.Effects.addProperty("ADBE CurvesCustom"); } catch (e) { return; }
            // NOTE: Curve shape data is CUSTOM_VALUE — cannot be set via script.
            // Open the effect and adjust curves manually.
        })();

        // 5. Brightness & Contrast
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("ADBE Brightness & Contrast 2"); } catch (e) { return; }
            setEffectProp(fx, "ADBE Brightness & Contrast 2-0001", 0);   // Brightness
            setEffectProp(fx, "ADBE Brightness & Contrast 2-0002", 10);  // Contrast
            setEffectProp(fx, "ADBE Brightness & Contrast 2-0003", 0);   // Use Legacy
        })();

        // 6. Looks
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("MB LookSuite3"); } catch (e) { return; }
            setEffectProp(fx, "MB LookSuite3-0013", 100);  // Strength
        })();

        // 7. Mojo II
        (function () {
            var fx;
            try { fx = al2.Effects.addProperty("Magic_Bullet_Mojo_II"); } catch (e) { return; }
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0001", 2);    // My Footage Is
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0002", 16);   // Preset
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0003", 13);   // Mojo
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0004", 100);  // Mojo Tint
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0005", -6);   // Punch It
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0006", 34);   // Bleach It
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0007", 0);    // Fade It
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0008", 0);    // Blue Squeeze
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0009", 0);    // Skin Squeeze
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0010", 0);    // Vignette It
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0012", 0);    // Exposure
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0013", 0);    // Cool/Warm
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0014", 0);    // Green/Magenta
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0015", 0);    // Skin Yellow/Pink
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0016", 0);    // Show Skin Overlay
            setEffectProp(fx, "Magic_Bullet_Mojo_II-0018", 100);  // Strength
        })();

        // ==================================================================
        //  ADJUSTMENT LAYER 4 — S_HueSatBright, Curves (top of the stack)
        // ==================================================================
        var al4 = addAdjustmentLayer("Adjustment Layer 4 — S_HueSatBright");

        // 1. S_HueSatBright
        (function () {
            var fx;
            try {
                fx = al4.Effects.addProperty("S_HueSatBright");
            } catch (e) {
                __sidecarLog("Sapphire S_HueSatBright plugin missing — skipping: " + e.toString());
                return;
            }
            setEffectProp(fx, "S_HueSatBright-0050", 0);    // Hue Shift
            setEffectProp(fx, "S_HueSatBright-0051", 0);    // Preserve Luma
            setEffectProp(fx, "S_HueSatBright-0052", 0.6);  // Saturation
            setEffectProp(fx, "S_HueSatBright-0053", 1);    // Brightness
            setEffectProp(fx, "S_HueSatBright-0055", 0);    // Offset Darks
            setEffectProp(fx, "S_HueSatBright-0540", 1);    // Mask Use
            setEffectProp(fx, "S_HueSatBright-0541", 12);   // Blur Mask
            setEffectProp(fx, "S_HueSatBright-0542", 0);    // Invert Mask
            // Scale Colors (RGBA)
            try {
                var scProp = fx.property("S_HueSatBright-0054");
                if (scProp) scProp.setValue([1, 1, 1, 1]);
            } catch (e) {}
        })();

        // 2. Curves (curve data must be set manually!)
        (function () {
            try { al4.Effects.addProperty("ADBE CurvesCustom"); } catch (e) { return; }
        })();

        // ==================================================================
        //  Reorder layers so the stack is correct (top-down in AE timeline):
        //    1. Adjustment Layer 4  (top — applied last)
        //    2. Adjustment Layer 2
        //    3. Adjustment Layer 3
        //    4. Adjustment Layer 1  (bottom — applied first)
        //
        //  Layers were added in order: al1, al3, al2, al4
        //  After adding, al4 is at index 1 (most recently added = topmost).
        //  AE adds each new layer above the previous, so the order should
        //  already be: al4(1), al2(2), al3(3), al1(4).
        //  That matches the desired stack. No reordering needed.
        // ==================================================================

        __sidecarLog("done — 4 adjustment layers created (cold, saturation=0.6)");

    } catch (err) {
        __sidecarLog("ERROR: " + err.toString() + " line=" + (err.line || "?"));
    }

    app.endUndoGroup();

})();
