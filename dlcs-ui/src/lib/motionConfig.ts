import type { Variants } from "framer-motion";

export const intelligenceSpring = {
  type: "spring",
  stiffness: 155,
  damping: 32,
  mass: 0.9,
  restDelta: 0.001,
} as const;

export const fadeEase = {
  duration: 0.18,
  ease: [0.23, 1, 0.32, 1],
} as const;

export const routeTransition = {
  duration: 0.2,
  ease: [0.22, 1, 0.36, 1],
} as const;

export type RouteDirection = -1 | 0 | 1;

export const routeVariants: Variants = {
  enter: (direction: RouteDirection = 0) => ({
    opacity: 0,
    x: direction === 0 ? 0 : direction > 0 ? 10 : -10,
  }),
  center: {
    opacity: 1,
    x: 0,
  },
  exit: (direction: RouteDirection = 0) => ({
    opacity: 0,
    x: direction === 0 ? 0 : direction > 0 ? -10 : 10,
  }),
};
