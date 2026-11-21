"use client";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";
import SignIn from "@/components/SignIn";
import { Loading, useSession } from "@/components/shell";

export default function LoginPage() {
  return (
    <Suspense fallback={<Loading />}>
      <Login />
    </Suspense>
  );
}

function Login() {
  const session = useSession();
  const params = useSearchParams();
  const router = useRouter();
  useEffect(() => {
    if (session) router.replace("/");
  }, [session, router]);
  if (session === undefined || session) return <Loading />;
  return <SignIn initialMode={params.get("mode") === "signup" ? "up" : "in"} after={typeof window !== "undefined" ? window.location.origin + "/" : "/"} />;
}
