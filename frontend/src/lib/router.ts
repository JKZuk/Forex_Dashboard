import { useEffect, useState } from "react";

/** Minimal hash router: "#/region/JP" -> ["region", "JP"]. */
export function useRoute(): string[] {
  const read = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [parts, setParts] = useState<string[]>(read);
  useEffect(() => {
    const on = (e: Event) => {
      setParts(read());
      if (e.isTrusted) window.scrollTo(0, 0); // real navigation, not an in-view selection
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return parts;
}

export const href = (...parts: string[]) => `#/${parts.map(encodeURIComponent).join("/")}`;
