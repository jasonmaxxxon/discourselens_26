import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { useIntelligenceStore } from "./store/intelligenceStore";
import "./styles/app.css";

async function bootstrap() {
  try {
    await useIntelligenceStore.persist.rehydrate();
  } catch {
    // Continue rendering even when persisted data is unavailable.
  }

  ReactDOM.createRoot(document.getElementById("root")!).render(
    <AppErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </AppErrorBoundary>
  );
}

void bootstrap();
