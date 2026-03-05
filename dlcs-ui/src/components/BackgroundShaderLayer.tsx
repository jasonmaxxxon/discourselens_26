import { useEffect, useMemo, useState } from "react";
import { FilmGrain, Shader, Swirl, WaveDistortion } from "shaders/react";
import { useIntelligenceStore } from "../store/intelligenceStore";

export function BackgroundShaderLayer() {
  const telemetryDegraded = useIntelligenceStore((s) => s.telemetryDegraded);
  const riskLevel = useIntelligenceStore((s) => s.riskLevel);
  const [canRenderShader] = useState(() => {
    if (typeof window === "undefined") return false;
    if (navigator.webdriver) return false;
    try {
      const canvas = document.createElement("canvas");
      return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
    } catch {
      return false;
    }
  });
  const [isHidden, setIsHidden] = useState(() => document.visibilityState === "hidden");
  const [reducedMotion, setReducedMotion] = useState(() =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );

  useEffect(() => {
    const onVisibility = () => setIsHidden(document.visibilityState === "hidden");
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onMedia = (evt: MediaQueryListEvent) => setReducedMotion(evt.matches);

    document.addEventListener("visibilitychange", onVisibility);
    media.addEventListener("change", onMedia);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      media.removeEventListener("change", onMedia);
    };
  }, []);

  const riskBias = useMemo(() => {
    const level = String(riskLevel || "").toLowerCase();
    if (level.includes("critical") || level.includes("high") || level.includes("高")) return 0.03;
    if (level.includes("low") || level.includes("低")) return -0.01;
    return 0;
  }, [riskLevel]);

  const swirlSpeed = reducedMotion ? 0 : isHidden ? 0.002 : Math.max(0.06, Math.min(0.1, 0.08 + riskBias));
  const waveSpeed = reducedMotion ? 0 : isHidden ? 0.002 : telemetryDegraded ? 0.1 : 0.075;
  const waveStrength = reducedMotion || isHidden ? 0 : telemetryDegraded ? 0.022 : 0.014;
  const grainStrength = isHidden ? 0.008 : telemetryDegraded ? 0.028 : 0.015;

  if (!canRenderShader) return null;

  return (
    <div className="shader-atmos-layer" aria-hidden>
      <Shader>
        <Swirl
          colorA="#7090c5"
          colorB="#cdd7e3"
          colorSpace="oklch"
          detail={1.6}
          speed={swirlSpeed}
        />
        <WaveDistortion
          angle={133}
          edges="mirror"
          frequency={1.15}
          speed={waveSpeed}
          strength={waveStrength}
          visible={!reducedMotion}
        />
        <FilmGrain strength={grainStrength} />
      </Shader>
    </div>
  );
}
