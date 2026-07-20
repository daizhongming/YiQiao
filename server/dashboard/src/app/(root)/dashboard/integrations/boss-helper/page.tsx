"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  Link2,
  RefreshCw,
  ShieldCheck,
  Unplug,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { api, getActiveProjectId } from "@/utils/api";
import { BOSS_HELPER_ENDPOINTS } from "@/utils/api-endpoints";

type PairingStatus =
  | "pending"
  | "approved"
  | "connected"
  | "expired"
  | "revoked"
  | "auth_expired";

type Pairing = {
  pairing_id: string;
  status: PairingStatus;
  project_id: string | null;
  scopes: string[];
  key_prefix: string | null;
  pairing_expires_at: string;
  key_expires_at: string | null;
  requested_at: string;
  approved_at: string | null;
  connected_at: string | null;
  revoked_at: string | null;
};

const STATUS_CLASSES: Record<PairingStatus, string> = {
  pending:
    "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200",
  approved:
    "border-blue-300 bg-blue-50 text-blue-800 dark:border-blue-800 dark:bg-blue-950/30 dark:text-blue-200",
  connected:
    "border-emerald-300 bg-emerald-50 text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200",
  expired:
    "border-neutral-300 bg-neutral-50 text-neutral-700 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200",
  revoked:
    "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200",
  auth_expired:
    "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/30 dark:text-rose-200",
};

function normalizeCode(value: string) {
  const compact = value
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 8);
  return compact.length > 4
    ? `${compact.slice(0, 4)}-${compact.slice(4)}`
    : compact;
}

function formatDate(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString();
}

export default function BossHelperIntegrationPage() {
  const { t } = useI18n();
  const [userCode, setUserCode] = useState("");
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [loading, setLoading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [revoking, setRevoking] = useState(false);

  const normalizedCode = useMemo(() => normalizeCode(userCode), [userCode]);
  const codeReady = normalizedCode.replace("-", "").length === 8;

  const loadStatus = useCallback(
    async (quiet = false) => {
      if (!codeReady) return;
      if (!quiet) setLoading(true);
      try {
        const response = await api.get<Pairing>(BOSS_HELPER_ENDPOINTS.STATUS, {
          params: { user_code: normalizedCode },
        });
        setPairing(response.data);
      } catch (error) {
        if (!quiet) {
          toast({
            title: t("Pairing request"),
            description: getErrorMessage(error),
            variant: "destructive",
          });
        }
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [codeReady, normalizedCode, t],
  );

  useEffect(() => {
    const queryCode = new URLSearchParams(window.location.search).get(
      "user_code",
    );
    if (queryCode) setUserCode(normalizeCode(queryCode));
  }, []);

  useEffect(() => {
    if (!codeReady || pairing) return;
    void loadStatus();
  }, [codeReady, loadStatus, pairing]);

  useEffect(() => {
    if (!pairing || !["pending", "approved"].includes(pairing.status)) return;
    const timer = window.setInterval(() => void loadStatus(true), 3000);
    return () => window.clearInterval(timer);
  }, [loadStatus, pairing]);

  const approve = async () => {
    if (!codeReady) return;
    setApproving(true);
    try {
      const response = await api.post<Pairing>(BOSS_HELPER_ENDPOINTS.APPROVE, {
        user_code: normalizedCode,
        project_id: getActiveProjectId(),
      });
      setPairing(response.data);
      toast({ title: t("Connection approved"), variant: "success" });
    } catch (error) {
      toast({
        title: t("Approve connection"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setApproving(false);
    }
  };

  const revoke = async () => {
    if (!pairing) return;
    setRevoking(true);
    try {
      const response = await api.post<Pairing>(BOSS_HELPER_ENDPOINTS.REVOKE, {
        pairing_id: pairing.pairing_id,
      });
      setPairing(response.data);
      toast({ title: t("Connection revoked"), variant: "success" });
    } catch (error) {
      toast({
        title: t("Revoke connection"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setRevoking(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="flex size-9 items-center justify-center rounded-md border border-memBorder-primary bg-surface-default-tertiary">
          <Link2 className="size-4" />
        </div>
        <h1 className="text-xl font-semibold font-fustat">
          {t("BossHelper connection")}
        </h1>
      </div>

      <div className="max-w-xl space-y-2">
        <Label htmlFor="boss-helper-user-code">{t("Authorization code")}</Label>
        <div className="flex gap-2">
          <Input
            id="boss-helper-user-code"
            value={normalizedCode}
            onChange={(event) => {
              setUserCode(event.target.value);
              setPairing(null);
            }}
            placeholder={t("Enter the code shown in BossHelper")}
            autoComplete="one-time-code"
            className="font-mono uppercase"
          />
          <Button
            type="button"
            variant="outline"
            size="icon"
            disabled={!codeReady || loading}
            onClick={() => void loadStatus()}
            aria-label={t("Refresh status")}
            title={t("Refresh status")}
          >
            <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
          </Button>
        </div>
      </div>

      {pairing && (
        <Card className="max-w-3xl border-memBorder-primary p-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="size-5" />
              <h2 className="text-base font-semibold">
                {t("Pairing request")}
              </h2>
            </div>
            <span
              className={`rounded-md border px-2 py-1 text-xs font-medium ${STATUS_CLASSES[pairing.status]}`}
            >
              {t(pairing.status)}
            </span>
          </div>

          <dl className="mt-5 grid gap-4 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Project")}
              </dt>
              <dd className="mt-1 font-mono">
                {pairing.project_id ?? getActiveProjectId()}
              </dd>
            </div>
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Key prefix")}
              </dt>
              <dd className="mt-1 font-mono">{pairing.key_prefix ?? "-"}</dd>
            </div>
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Requested at")}
              </dt>
              <dd className="mt-1">{formatDate(pairing.requested_at)}</dd>
            </div>
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Pairing expires at")}
              </dt>
              <dd className="mt-1">{formatDate(pairing.pairing_expires_at)}</dd>
            </div>
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Credential expires at")}
              </dt>
              <dd className="mt-1">{formatDate(pairing.key_expires_at)}</dd>
            </div>
            <div>
              <dt className="text-onSurface-default-secondary">
                {t("Scopes")}
              </dt>
              <dd className="mt-1 font-mono text-xs">
                {pairing.scopes.join(", ")}
              </dd>
            </div>
          </dl>

          <div className="mt-6 flex flex-wrap gap-2">
            {pairing.status === "pending" && (
              <Button disabled={approving} onClick={() => void approve()}>
                <CheckCircle2 className="mr-1 size-4" />
                {t("Approve connection")}
              </Button>
            )}
            {["approved", "connected"].includes(pairing.status) && (
              <Button
                variant="destructive"
                disabled={revoking}
                onClick={() => void revoke()}
              >
                <Unplug className="mr-1 size-4" />
                {t("Revoke connection")}
              </Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
