"use client";

import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export interface Suggestion {
  value: string;
  label: string;
  hint?: string;
}

interface SuggestInputProps {
  value: string;
  onChange: (value: string) => void;
  onPick?: (item: Suggestion) => void;
  suggestions?: Suggestion[];
  onQuery?: (query: string) => Promise<Suggestion[]>;
  placeholder?: string;
  className?: string;
  inputClassName?: string;
  required?: boolean;
  disabled?: boolean;
  id?: string;
  leftIcon?: React.ReactNode;
  maxSuggestions?: number;
  emptyLabel?: string;
}

function mergeSuggestions(
  staticItems: Suggestion[],
  remoteItems: Suggestion[],
  query: string,
  max: number
): Suggestion[] {
  const needle = query.trim().toLowerCase();
  const seen = new Set<string>();
  const merged: Suggestion[] = [];

  for (const item of [...staticItems, ...remoteItems]) {
    if (seen.has(item.value)) continue;
    const haystack = `${item.value} ${item.label} ${item.hint ?? ""}`.toLowerCase();
    if (needle && !haystack.includes(needle)) continue;
    seen.add(item.value);
    merged.push(item);
    if (merged.length >= max) break;
  }
  return merged;
}

export function SuggestInput({
  value,
  onChange,
  suggestions = [],
  onQuery,
  onPick,
  placeholder,
  className,
  inputClassName,
  required,
  disabled,
  id,
  leftIcon,
  maxSuggestions = 8,
  emptyLabel,
}: SuggestInputProps) {
  const autoId = useId();
  const inputId = id ?? autoId;
  const listId = `${inputId}-suggestions`;
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [remote, setRemote] = useState<Suggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);

  useEffect(() => {
    if (!onQuery || !open) return;

    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      void onQuery(value)
        .then((items) => {
          if (!cancelled) setRemote(items);
        })
        .catch(() => {
          if (!cancelled) setRemote([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 180);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [onQuery, open, value]);

  const visible = useMemo(
    () => mergeSuggestions(suggestions, remote, value, maxSuggestions),
    [suggestions, remote, value, maxSuggestions]
  );

  const pick = useCallback(
    (item: Suggestion) => {
      onChange(item.value);
      onPick?.(item);
      setOpen(false);
      setActiveIndex(-1);
    },
    [onChange, onPick]
  );

  useEffect(() => {
    const onDocClick = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
        setActiveIndex(-1);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, []);

  return (
    <div ref={rootRef} className={cn("relative", className)}>
      {leftIcon ? (
        <div className="relative">
          <div className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-bone-dim">
            {leftIcon}
          </div>
          <Input
            id={inputId}
            value={value}
            onChange={(event) => {
              onChange(event.target.value);
              setOpen(true);
              setActiveIndex(-1);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={(event) => {
              if (!open || visible.length === 0) return;
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActiveIndex((idx) => (idx + 1) % visible.length);
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActiveIndex((idx) => (idx <= 0 ? visible.length - 1 : idx - 1));
              } else if (event.key === "Enter" && activeIndex >= 0) {
                event.preventDefault();
                pick(visible[activeIndex]);
              } else if (event.key === "Escape") {
                setOpen(false);
                setActiveIndex(-1);
              }
            }}
            placeholder={placeholder}
            className={cn("pl-9", inputClassName)}
            required={required}
            disabled={disabled}
            autoComplete="off"
            role="combobox"
            aria-expanded={open}
            aria-controls={listId}
            aria-autocomplete="list"
          />
        </div>
      ) : (
        <Input
          id={inputId}
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            setOpen(true);
            setActiveIndex(-1);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (!open || visible.length === 0) return;
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setActiveIndex((idx) => (idx + 1) % visible.length);
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setActiveIndex((idx) => (idx <= 0 ? visible.length - 1 : idx - 1));
            } else if (event.key === "Enter" && activeIndex >= 0) {
              event.preventDefault();
              pick(visible[activeIndex]);
            } else if (event.key === "Escape") {
              setOpen(false);
              setActiveIndex(-1);
            }
          }}
          placeholder={placeholder}
          className={inputClassName}
          required={required}
          disabled={disabled}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-controls={listId}
          aria-autocomplete="list"
        />
      )}

      {open && (
        <div
          id={listId}
          role="listbox"
          className="absolute z-40 mt-1 w-full border border-hairline bg-vault-raised shadow-lg max-h-52 overflow-y-auto custom-scrollbar"
        >
          {loading && visible.length === 0 ? (
            <p className="px-3 py-2 mono-meta normal-case">{emptyLabel ?? "…"}</p>
          ) : visible.length === 0 ? (
            <p className="px-3 py-2 text-xs text-bone-dim">
              {emptyLabel ?? "No matches"}
            </p>
          ) : (
            visible.map((item, index) => (
              <button
                key={item.value}
                type="button"
                role="option"
                aria-selected={index === activeIndex}
                className={cn(
                  "w-full text-left px-3 py-2 border-b border-hairline/60 last:border-b-0 transition-colors",
                  index === activeIndex
                    ? "bg-[rgba(232,178,58,0.12)]"
                    : "hover:bg-[rgba(232,230,223,0.04)]"
                )}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => pick(item)}
              >
                <p className="font-mono text-xs text-bone truncate">{item.value}</p>
                <p className="text-[0.6875rem] text-bone-dim truncate">
                  {item.label}
                  {item.hint ? ` · ${item.hint}` : ""}
                </p>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
