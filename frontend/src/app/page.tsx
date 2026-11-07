import { redirect } from "next/navigation";

/**
 * The marketing landing page arrives post-MVP; for now the root always
 * funnels into the app. Middleware decides between /dashboard and /login.
 */
export default function HomePage() {
  redirect("/dashboard");
}
