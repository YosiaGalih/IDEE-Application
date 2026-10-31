import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles/index.css";
import i18n from "./i18n";
import { App } from "./app/App";

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");
document.documentElement.lang = i18n.language;

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
