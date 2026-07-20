// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";
import { Check, Copy } from "lucide-react";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/hooks/use-auth";
import { getErrorMessage } from "@/lib/error-message";
import { isValidEmail } from "@/lib/validators";
import { LanguageToggle } from "@/components/i18n/language-toggle";
import { useI18n } from "@/lib/i18n";

const RESET_COMMAND =
  "make reset-admin-password EMAIL=<your-email> PASSWORD=<new-password>";

export default function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isLoading, login, register } = useAuth();
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isRegisteringInvite, setIsRegisteringInvite] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [mounted, setMounted] = useState(false);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!isLoading && user) {
      router.push(searchParams.get("next") || "/dashboard/requests");
    }
  }, [user, isLoading, router, searchParams]);

  const emailValid = isValidEmail(email);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!emailValid) {
      setError("Enter a valid email address.");
      return;
    }
    if (isRegisteringInvite && !name.trim()) {
      setError("Enter your name.");
      return;
    }
    setSubmitting(true);
    try {
      if (isRegisteringInvite) {
        await register(name.trim(), email, password);
      } else {
        await login(email, password);
      }
      router.push(searchParams.get("next") || "/dashboard/requests");
    } catch (err) {
      setError(getErrorMessage(err, "Login failed"));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex min-h-screen bg-surface-default-secondary">
      <div className="fixed right-4 top-4 z-20">
        <LanguageToggle compact />
      </div>
      <div className="flex w-full items-center justify-center p-6 sm:p-8">
        <div className="w-full max-w-md">
          <div className="flex justify-center mb-2">
            {mounted && (
              <Image src="/favicon.svg" alt="YiQiao" width={41} height={41} />
            )}
          </div>
          <h1 className="text-2xl font-semibold text-onSurface-default-primary text-center mb-6 font-fustat">
            {t(
              isRegisteringInvite
                ? "Create invited account"
                : "Sign in to YiQiao",
            )}
          </h1>
          <div className="flex flex-col gap-4 rounded-lg border border-memBorder-primary bg-surface-default-primary p-6 sm:p-8">
            {error && (
              <p className="text-sm text-onSurface-danger-primary bg-surface-danger-primary px-3 py-2 rounded">
                {t(error)}
              </p>
            )}
            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              {isRegisteringInvite && (
                <div className="space-y-1.5">
                  <Label htmlFor="invite-name">{t("Name")}</Label>
                  <Input
                    id="invite-name"
                    name="name"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder={t("Your name")}
                    autoComplete="name"
                    required
                  />
                </div>
              )}
              <div className="space-y-1.5">
                <Label htmlFor="login-email">{t("Email")}</Label>
                <Input
                  id="login-email"
                  name="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="admin@company.com"
                  autoComplete="username"
                  required
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="login-password">{t("Password")}</Label>
                <Input
                  id="login-password"
                  name="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={
                    isRegisteringInvite ? "new-password" : "current-password"
                  }
                  required
                />
              </div>
              <Button
                type="submit"
                disabled={
                  submitting ||
                  !emailValid ||
                  !password ||
                  (isRegisteringInvite && !name.trim())
                }
                variant="default"
                size="lg"
                className="w-full"
              >
                {t(
                  submitting
                    ? isRegisteringInvite
                      ? "Creating..."
                      : "Signing in..."
                    : isRegisteringInvite
                      ? "Create account"
                      : "Sign in",
                )}
              </Button>
            </form>
            <button
              type="button"
              onClick={() => {
                setError("");
                setIsRegisteringInvite((value) => !value);
              }}
              className="text-xs text-onSurface-default-tertiary hover:text-onSurface-default-primary underline underline-offset-4 self-center"
            >
              {t(
                isRegisteringInvite
                  ? "Already have an account?"
                  : "Have an invite?",
              )}
            </button>
            <Dialog>
              <DialogTrigger asChild>
                <button
                  type="button"
                  className="text-xs text-onSurface-default-tertiary hover:text-onSurface-default-primary underline underline-offset-4 self-center"
                >
                  {t("Forgot password?")}
                </button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>{t("Reset your admin password")}</DialogTitle>
                  <DialogDescription>
                    {t(
                      "Run this command on the server host. It overwrites the existing password; anyone already signed in stays signed in until their session expires.",
                    )}
                  </DialogDescription>
                </DialogHeader>
                <div className="flex gap-2">
                  <Input
                    readOnly
                    value={RESET_COMMAND}
                    className="font-mono text-xs"
                  />
                  <CopyToClipboard
                    text={RESET_COMMAND}
                    onCopy={() => {
                      setCopied(true);
                      setTimeout(() => setCopied(false), 2000);
                    }}
                  >
                    <Button
                      variant="outline"
                      size="icon"
                      aria-label={t(copied ? "Copied" : "Copy reset command")}
                      title={t(copied ? "Copied" : "Copy reset command")}
                    >
                      {copied ? (
                        <Check className="size-4" />
                      ) : (
                        <Copy className="size-4" />
                      )}
                    </Button>
                  </CopyToClipboard>
                </div>
              </DialogContent>
            </Dialog>
          </div>
        </div>
      </div>
    </div>
  );
}
