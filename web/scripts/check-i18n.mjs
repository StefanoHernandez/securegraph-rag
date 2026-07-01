import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("..", import.meta.url).pathname;

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function readJson(relativePath) {
  return JSON.parse(readFileSync(join(root, relativePath), "utf8"));
}

const packageJson = readJson("package.json");
assert(packageJson.dependencies?.["next-intl"], "next-intl must be installed");

for (const locale of ["it", "en"]) {
  const messagesPath = `messages/${locale}.json`;
  assert(existsSync(join(root, messagesPath)), `missing ${locale} messages`);
  const messages = readJson(messagesPath);
  for (const key of ["briefing", "query", "settings", "operations"]) {
    assert(messages.navigation?.[key], `${locale} missing navigation.${key}`);
  }
  for (const key of ["operatorMenu", "profile", "logout", "tenant"]) {
    assert(messages.shell?.[key], `${locale} missing shell.${key}`);
  }
  for (const key of ["title", "providerCredentials", "runtimeSetting", "saveSetting"]) {
    assert(messages.settingsPage?.[key], `${locale} missing settingsPage.${key}`);
  }
  for (const key of ["title", "identity", "password", "activeSessions", "changePassword"]) {
    assert(messages.profilePage?.[key], `${locale} missing profilePage.${key}`);
  }
  for (const namespace of [
    "usersPage",
    "tenantsPage",
    "groupsPage",
    "accessTemplatesPage",
    "operationsPage",
    "modelsPage",
    "healthPage",
    "permissionsPage",
    "chatPage",
    "ingestPage",
    "auditPage",
    "graphPage",
    "explainPage",
    "loginPage",
  ]) {
    for (const key of ["title", "refresh", "create", "error"]) {
      assert(messages[namespace]?.[key], `${locale} missing ${namespace}.${key}`);
    }
  }
}

const routingPath = join(root, "src/i18n/routing.ts");
assert(existsSync(routingPath), "missing src/i18n/routing.ts");
const routing = readFileSync(routingPath, "utf8");
assert(routing.includes('"it"') && routing.includes('"en"'), "routing must define it/en");
assert(routing.includes('defaultLocale: "it"'), "routing must default to it");

const proxyPath = join(root, "src/proxy.ts");
assert(existsSync(proxyPath), "missing src/proxy.ts");
const proxy = readFileSync(proxyPath, "utf8");
assert(proxy.includes("createMiddleware"), "proxy must use next-intl middleware");
assert(proxy.includes("routing"), "proxy must use shared routing config");

console.log("i18n structure OK");
