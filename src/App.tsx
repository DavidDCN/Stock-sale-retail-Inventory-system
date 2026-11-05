import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import AppShell from "./layout/AppShell";

// Each page loads only when it is opened (smaller first load, no "chunk larger than 500 kB" warning).
const MainPage = lazy(() => import("./pages/main"));
const SalesPage = lazy(() => import("./pages/sales"));
const SupplyPage = lazy(() => import("./pages/supply"));
const CashflowPage = lazy(() => import("./pages/cashflow"));
const ProgressionPage = lazy(() => import("./pages/progression"));
const CalendarPage = lazy(() => import("./pages/calendar"));
const PaymentsPage = lazy(() => import("./pages/payments"));
const TransferPage = lazy(() => import("./pages/transfer"));
const PlannerPage = lazy(() => import("./pages/planner"));

const page = (el: React.ReactNode) => (
  <Suspense fallback={<p className="p-6 text-xs" style={{ color: "var(--muted)" }}>Loading…</p>}>{el}</Suspense>
);

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={page(<MainPage />)} />
          <Route path="sales" element={page(<SalesPage />)} />
          <Route path="supply" element={page(<SupplyPage />)} />
          <Route path="cashflow" element={page(<CashflowPage />)} />
          <Route path="progression" element={page(<ProgressionPage />)} />
          <Route path="calendar" element={page(<CalendarPage />)} />
          <Route path="payments" element={page(<PaymentsPage />)} />
          <Route path="transfer" element={page(<TransferPage />)} />
          <Route path="planner" element={page(<PlannerPage />)} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
