function createMemoryStorage(): Storage {
  const entries = new Map<string, string>();
  return {
    get length() {
      return entries.size;
    },
    clear() {
      entries.clear();
    },
    getItem(key) {
      return entries.get(key) ?? null;
    },
    key(index) {
      return [...entries.keys()][index] ?? null;
    },
    removeItem(key) {
      entries.delete(key);
    },
    setItem(key, value) {
      entries.set(key, String(value));
    },
  };
}

const local = createMemoryStorage();
const session = createMemoryStorage();

Object.defineProperty(globalThis, "localStorage", { configurable: true, value: local });
Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: session });
Object.defineProperty(window, "localStorage", { configurable: true, value: local });
Object.defineProperty(window, "sessionStorage", { configurable: true, value: session });
