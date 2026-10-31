import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { createQueryClient } from "@/api/queryClient";
import { routes } from "./routes";

const router = createBrowserRouter(routes);
const goToLogin = () => {
  if (router.state.location.pathname !== "/login") void router.navigate("/login");
};

export function App() {
  const [queryClient] = useState(() => createQueryClient({ onUnauthenticated: goToLogin }));
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
