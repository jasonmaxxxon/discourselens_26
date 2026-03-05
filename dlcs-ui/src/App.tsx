import { useEffect, useMemo } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { AnimatePresence, motion } from "framer-motion";
import { MainLayout } from "./components/MainLayout";
import { OverviewPage } from "./pages/OverviewPage";
import { PipelinePage } from "./pages/PipelinePage";
import { InsightsPage } from "./pages/InsightsPage";
import { LibraryPage } from "./pages/LibraryPage";
import { ReviewPage } from "./pages/ReviewPage";
import { StitchOverviewPage } from "./pages/StitchOverviewPage";
import { StitchPipelinePage } from "./pages/StitchPipelinePage";
import { StitchInsightsPage } from "./pages/StitchInsightsPage";
import { StitchLibraryPage } from "./pages/StitchLibraryPage";
import { StitchReviewPage } from "./pages/StitchReviewPage";
import { StitchGlobalTopBar } from "./components/StitchGlobalTopBar";

const PRIMARY_ORDER = ["/overview", "/pipeline", "/insights", "/library", "/review"];

function canonicalPrimaryPath(pathname: string): string {
  const clean = pathname.replace(/\/+$/, "");
  if (clean === "/" || clean === "/dashboard") return "/overview";
  if (clean === "/insight") return "/insights";
  return PRIMARY_ORDER.find((route) => clean === route || clean.startsWith(`${route}/`)) || "/overview";
}

function LegacyRoutes() {
  return (
    <MainLayout>
      <Routes>
        <Route path="/legacy" element={<Navigate to="/legacy/overview" replace />} />
        <Route path="/legacy/overview" element={<OverviewPage />} />
        <Route path="/legacy/pipeline" element={<PipelinePage />} />
        <Route path="/legacy/insights" element={<InsightsPage />} />
        <Route path="/legacy/library" element={<LibraryPage />} />
        <Route path="/legacy/review" element={<ReviewPage />} />
        <Route path="*" element={<Navigate to="/legacy/overview" replace />} />
      </Routes>
    </MainLayout>
  );
}

export function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const isLegacy = location.pathname.startsWith("/legacy");
  const activePath = useMemo(() => canonicalPrimaryPath(location.pathname), [location.pathname]);

  useEffect(() => {
    if (isLegacy) return;
    const clean = location.pathname.replace(/\/+$/, "");
    if (clean !== activePath) {
      navigate(activePath + location.search, { replace: true });
    }
  }, [activePath, isLegacy, location.pathname, location.search, navigate]);

  if (isLegacy) {
    return <LegacyRoutes />;
  }

  const pageMap = {
    "/overview": <StitchOverviewPage />,
    "/pipeline": <StitchPipelinePage />,
    "/insights": <StitchInsightsPage />,
    "/library": <StitchLibraryPage />,
    "/review": <StitchReviewPage />,
  } as const;
  const activePage = pageMap[activePath as keyof typeof pageMap] || pageMap["/overview"];

  return (
    <div className="stitch-app-shell">
      <StitchGlobalTopBar />
      <main className="stitch-shell-main">
        <AnimatePresence mode="wait" initial={false}>
          <motion.section
            key={activePath}
            className="stitch-page active"
            initial={{ opacity: 0, y: 8, scale: 0.997 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.997 }}
            transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          >
            {activePage}
          </motion.section>
        </AnimatePresence>
      </main>
    </div>
  );
}
