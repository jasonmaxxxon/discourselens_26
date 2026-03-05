import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { fadeEase } from "../lib/motionConfig";

type Props = {
  title: string;
  subtitle: string;
  actions?: ReactNode;
  identityName?: string;
};

export function PageHeader({ title, subtitle, actions, identityName = "dl-page-identity" }: Props) {
  return (
    <div className="page-head">
      <div className="page-head-copy identity-anchor">
        <motion.h1 layoutId={identityName} transition={fadeEase}>{title}</motion.h1>
        <motion.p initial={{ opacity: 0.6 }} animate={{ opacity: 1 }} transition={fadeEase}>
          {subtitle}
        </motion.p>
      </div>
      {actions ? <div className="head-actions">{actions}</div> : null}
    </div>
  );
}
