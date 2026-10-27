# CTA timer experiments · 11 October 2026

Requested via Context7. The selected Контур design is now a corner CTA with a deterministic 40-second demo clock. The first 10 seconds hold 50% with no visible timer; the next 30 seconds hold 30% while the timer counts down. Previous Уровень and Лента variants share the same corrected timing. These are authored demonstration offer phases. The selected perimeter presentation is shared with the website; no checkout or payment integration is connected.

## Reused / adapted

- NumberFlow 0.6.2 (MIT), installed as @number-flow/react: https://number-flow.barvian.me/. Real rolling numeric component, with reduced-motion support. Context7 `/barvian/number-flow`, queried React countdown and timing. `animated` follows the lab player, so pause/scrub stops digit motion. License in public/third-party-cta.txt.
- Countdown Circle Timer (MIT): https://github.com/vydimitrov/react-countdown-circle-timer/blob/master/packages/shared/src/useCountdown.ts. Found with Context7 grep `colorsTime`: time-based interpolation of color and SVG stroke. Adapted concept to a rectangular CTA outline, driven by our existing lab clock; no second timer or new timer dependency.
- Liquid Fill Gauge: https://github.com/looker-open-source/sdk-codegen/blob/main/packages/extension-tile-playground/src/components/LiquidFillGaugeViz/liquid_fill_gauge.js. Context7 grep `waveHeightScaling` explains suppressing waves at 0% and 100%. Original SVG path and Motion transforms in our button, no copied D3 implementation.
- Motion docs via Context7 `/websites/motion_dev`: https://motion.dev/docs/transform and https://motion.dev/docs/svg-animation. Existing installed Motion 13.4.6 drives color, level, outline and ribbon from the same progress value.

## Designs

1. Уровень: translucent liquid retreats downward, subtle wave disappears at endpoints.
2. Контур: remaining-time stroke shrinks around the button perimeter.
3. Лента: segmented discount strip retracts with a moving marker.

The accepted presentation puts the button in the upper-right corner of the page header. It has no subtitle, separator or arrow. The timer uses oversized translucent numerals behind the action and fixed percentage. The perimeter and color begin changing after the ten-second opening period. At expiry the timer, offer and outline disappear; the action remains. Replay/pause/seek and the existing local demo brief remain available. All state comes from the lab player; no independent clock or automatic expiry loop is added. The selected perimeter button is now also the main website’s corner CTA. Both surfaces render the same CountdownButton. The site derives progress from monotonic time since mount; page scrolling and the brief dialog do not restart it. No new timer starts automatically at expiry. The lab retains pause, seek and replay controls.
