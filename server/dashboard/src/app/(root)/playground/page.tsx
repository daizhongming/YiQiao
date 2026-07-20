"use client";

import Link from "next/link";
import { useState } from "react";
import { MessageSquarePlus, Send, Settings } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { api } from "@/utils/api";
import { PLAYGROUND_ENDPOINTS } from "@/utils/api-endpoints";
import { Memory } from "@/types/api";

type ChatMessage = { role: "user" | "assistant"; content: string };

const MAX_HISTORY_MESSAGES = 20;
const MAX_HISTORY_MESSAGE_LENGTH = 50_000;
const MAX_MESSAGE_LENGTH = 10_000;
const MAX_USER_ID_LENGTH = 255;

const getMemoryCategories = (memory: Memory) => {
  if (memory.categories?.length) {
    return memory.categories;
  }
  return typeof memory.metadata?.category === "string"
    ? [memory.metadata.category]
    : [];
};

export default function PlaygroundPage() {
  const { t } = useI18n();
  const [input, setInput] = useState("");
  const [userId, setUserId] = useState("playground-user");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [sending, setSending] = useState(false);

  const send = async () => {
    if (sending) return;
    const text = input.trim();
    if (!text) return;
    if (text.length > MAX_MESSAGE_LENGTH) {
      toast({
        title: t("Message is too long"),
        description: `${t("Maximum length")}: ${MAX_MESSAGE_LENGTH.toLocaleString()}`,
        variant: "destructive",
      });
      return;
    }
    const scopedUserId = userId.trim();
    if (!scopedUserId) {
      toast({
        title: t("User ID is required"),
        variant: "destructive",
      });
      return;
    }
    if (scopedUserId.length > MAX_USER_ID_LENGTH) {
      toast({
        title: t("User ID is too long"),
        description: `${t("Maximum length")}: ${MAX_USER_ID_LENGTH}`,
        variant: "destructive",
      });
      return;
    }
    const previousMessages = messages;
    const requestHistory = previousMessages
      .slice(-MAX_HISTORY_MESSAGES)
      .map((message) => ({
        ...message,
        content: message.content.slice(0, MAX_HISTORY_MESSAGE_LENGTH),
      }));
    setInput("");
    setMessages([...previousMessages, { role: "user", content: text }]);
    setSending(true);
    try {
      const res = await api.post<{
        reply: string;
        memories: Memory[];
      }>(PLAYGROUND_ENDPOINTS.CHAT, {
        message: text,
        user_id: scopedUserId,
        history: requestHistory,
      });
      setMessages([
        ...previousMessages,
        { role: "user", content: text },
        { role: "assistant", content: res.data.reply },
      ]);
      setMemories(res.data.memories ?? []);
    } catch (error) {
      toast({
        title: "Playground request failed",
        description: getErrorMessage(error),
        variant: "destructive",
      });
      setMessages(previousMessages);
      setInput(text);
    } finally {
      setSending(false);
    }
  };

  const startNewConversation = () => {
    setInput("");
    setMessages([]);
    setMemories([]);
  };

  return (
    <div className="grid min-h-screen grid-cols-1 bg-surface-default-primary text-onSurface-default-primary lg:grid-cols-[minmax(0,1fr)_320px]">
      <main className="flex min-h-[70vh] min-w-0 flex-col lg:min-h-screen">
        <header className="flex h-12 items-center justify-between border-b border-memBorder-primary px-4">
          <Link
            href="/dashboard"
            className="text-sm underline underline-offset-4"
          >
            Back to Dashboard
          </Link>
          <div className="flex items-center gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={startNewConversation}
                  disabled={sending}
                  aria-label={t("New conversation")}
                >
                  <MessageSquarePlus className="size-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>{t("New conversation")}</TooltipContent>
            </Tooltip>
            <Button asChild variant="outline" size="sm">
              <Link href="/dashboard/settings?tab=project-playground">
                <Settings className="mr-2 size-4" />
                Settings
              </Link>
            </Button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-auto p-6">
          {messages.length === 0 ? (
            <div className="grid h-full place-items-center">
              <div className="max-w-xl text-center">
                <h1 className="text-2xl font-semibold">Playground</h1>
                <p className="mt-2 text-sm text-onSurface-default-secondary">
                  Test memory addition and retrieval with your local model
                  configuration.
                </p>
              </div>
            </div>
          ) : (
            messages.map((message, index) => (
              <Card
                key={index}
                className={
                  message.role === "user"
                    ? "ml-auto max-w-2xl border-memBorder-primary"
                    : "mr-auto max-w-2xl border-memBorder-primary"
                }
              >
                <CardContent className="p-4 text-sm">
                  {message.content}
                </CardContent>
              </Card>
            ))
          )}
        </div>

        <div className="border-t border-memBorder-primary p-4">
          <div className="mb-3 max-w-xs">
            <label
              htmlFor="playground-user-id"
              className="mb-1 block text-xs font-medium text-onSurface-default-secondary"
            >
              User ID
            </label>
            <Input
              id="playground-user-id"
              value={userId}
              onChange={(event) => setUserId(event.target.value)}
              disabled={sending || messages.length > 0}
              maxLength={MAX_USER_ID_LENGTH}
              autoComplete="off"
            />
          </div>
          <div className="flex gap-2">
            <Textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  void send();
                }
              }}
              placeholder="Type a message"
              className="min-h-12"
              disabled={sending}
              maxLength={MAX_MESSAGE_LENGTH}
            />
            <Button
              onClick={send}
              disabled={sending || !input.trim() || !userId.trim()}
              aria-label={t("Send message")}
              title={t("Send message")}
            >
              <Send className="size-4" />
            </Button>
          </div>
        </div>
      </main>

      <aside className="border-t border-memBorder-primary p-4 lg:border-l lg:border-t-0">
        <h2 className="text-sm font-semibold">Memories</h2>
        <div className="mt-4 space-y-3">
          {memories.length === 0 ? (
            <p className="text-sm text-onSurface-default-secondary">
              Relevant memories appear after a search.
            </p>
          ) : (
            memories.map((memory) => {
              const categories = getMemoryCategories(memory);
              return (
                <Card key={memory.id} className="border-memBorder-primary">
                  <CardContent className="space-y-2 p-3 text-xs">
                    <p>{memory.memory}</p>
                    {categories.length ? (
                      <p className="text-onSurface-default-tertiary">
                        {categories.join(", ")}
                      </p>
                    ) : null}
                  </CardContent>
                </Card>
              );
            })
          )}
        </div>
      </aside>
    </div>
  );
}
