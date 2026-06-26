import { lazy, Suspense, useState } from "react";
import Header from "./components/Header";

const Control = lazy(() => import("./pages/Control"));
const Overview = lazy(() => import("./pages/Overview"));
const Knowledge = lazy(() => import("./pages/Knowledge"));

type Tab = "control" | "overview" | "knowledge";

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>("control");

  return (
    <div className="h-screen flex flex-col overflow-hidden">
      <Header activeTab={activeTab} onTabChange={setActiveTab} />

      <div className="flex-1 min-h-0 grid grid-cols-1">
        <Suspense
          fallback={
            <main className="col-span-full min-h-0 overflow-auto bg-[#f6f8f6] px-6 py-5 text-sm text-[#5f6f6b]">
              Loading...
            </main>
          }
        >
          {activeTab === "control" && <Control />}
          {activeTab === "overview" && <Overview />}
          {activeTab === "knowledge" && <Knowledge />}
        </Suspense>
      </div>
    </div>
  );
}
