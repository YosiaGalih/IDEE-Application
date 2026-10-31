import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router";
import { createQueryClient } from "@/api/queryClient";
import { routes } from "./routes";
import { redirectToLogin } from "./session";

const router = createBrowserRouter(routes);

export function App() {
  const [queryClient] = useState(() =>
    createQueryClient({ onUnauthenticated: () => redirectToLogin(router) }),
  );
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  );
}
