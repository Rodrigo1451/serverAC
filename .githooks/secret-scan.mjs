// Varredura de segredo no que está indo pro commit. Quem chama é o
// .githooks/pre-commit — e ele roda também no D:\emergency-push.cmd, que faz
// `git add .` sem ninguém olhar o diff (ver "Commit de emergência" no
// CLAUDE.md). O .gitignore continua sendo a 1ª barreira; esta pega a chave
// colada dentro de um arquivo versionado e o `.env` forçado com `git add -f`.
//
// O achado diz arquivo, linha e tipo, NUNCA o valor: o log do hook pode
// parar num terminal compartilhado ou num print.
//
// FONTE ÚNICA: os outros repositórios de C:\GitHub levam uma cópia idêntica em
// .githooks/secret-scan.mjs (2026-10-16). Os testes moram aqui, em
// siteHive/src/lib/secret-scan.test.ts; mudou aqui, recopie pra lá.
//
// Uso: node scripts/secret-scan.mjs --staged   (o que está no índice)

import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

// Os padrões exigem o corpo da chave, não só o prefixo: a documentação cita
// `ak_test_…` e os testes usam `ak_test_abc`, e nada disso é segredo.
const PATTERNS = [
  { kind: "chave do Pagar.me/Stripe", re: /\b(?:ak|ek|sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}/ },
  { kind: "chave da AWS", re: /\b(?:AKIA|ASIA)[0-9A-Z]{16}\b/ },
  { kind: "chave da Anthropic", re: /\bsk-ant-[A-Za-z0-9_-]{20,}/ },
  { kind: "chave da OpenAI", re: /\bsk-(?!ant-)(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}/ },
  { kind: "chave do Google", re: /\bAIza[0-9A-Za-z_-]{35}\b/ },
  { kind: "chave do Brevo", re: /\bxkeysib-[a-f0-9]{32,}/ },
  { kind: "token do GitHub", re: /\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})/ },
  { kind: "chave secreta do Supabase", re: /\bsb_secret_[A-Za-z0-9_-]{20,}/ },
  { kind: "chave privada", re: /-----BEGIN [A-Z ]*PRIVATE KEY-----/ },
];

// JWT assinado (service_role do Supabase, token da SuperFrete). A assinatura
// HS256 tem 43 caracteres; o exemplo do jwt.io nos testes tem menos de 20.
// A chave `anon` do Supabase passa: ela vai pro navegador por desenho (quem
// protege os dados é o RLS), e o tracadoIdeal a versiona no vite.config.
const JWT = /\beyJ[A-Za-z0-9_-]{10,}\.(eyJ[A-Za-z0-9_-]{10,})\.[A-Za-z0-9_-]{20,}/g;

function isPublicJwt(payload) {
  try {
    return JSON.parse(Buffer.from(payload, "base64url").toString("utf8")).role === "anon";
  } catch {
    return false;
  }
}

// URL de banco com senha. Vale só banco descartável local (`:dev@`, `:test@`)
// e texto de exemplo — mesma regra do CLAUDE.md pra docs/.
const DB_URL = /\bpostgres(?:ql)?:\/\/[^\s:/@]+:([^\s@]+)@/g;
const DB_OK_PASSWORDS = new Set(["dev", "test", "senha", "password", "postgres", "x"]);

function dbPasswordIsReal(password) {
  if (DB_OK_PASSWORDS.has(password.toLowerCase())) return false;
  // variável de ambiente ou marcador de exemplo: ${...}, $VAR, <senha>, SENHA
  if (/[${}<>]/.test(password) || /^[A-Z_]+$/.test(password)) return false;
  return true;
}

// Linha com chave de mentira de propósito (teste do redator de relatos, que
// confere que a chave some do texto) leva este marcador. É uma escolha
// explícita e visível no diff, não uma exceção por pasta.
const FAKE_MARKER = "secret-scan: falso";

/** @param {string} text @returns {{ line: number, kind: string }[]} */
export function findSecrets(text) {
  const hits = [];
  text.split(/\r?\n/).forEach((content, i) => {
    if (content.includes(FAKE_MARKER)) return;
    for (const { kind, re } of PATTERNS) {
      if (re.test(content)) hits.push({ line: i + 1, kind });
    }
    for (const m of content.matchAll(JWT)) {
      if (!isPublicJwt(m[1])) hits.push({ line: i + 1, kind: "JWT" });
    }
    for (const m of content.matchAll(DB_URL)) {
      if (dbPasswordIsReal(m[1])) hits.push({ line: i + 1, kind: "URL de banco com senha" });
    }
  });
  return hits;
}

/**
 * `.env*`, chave e certificado não entram nem forçados. Modelo de `.env`
 * (`.example`/`.exemplo`/`.sample`/`.template`) passa pelo nome, e o
 * conteúdo dele continua sendo varrido como o de qualquer arquivo.
 */
export function isForbiddenPath(path) {
  const name = path.split(/[\\/]/).pop() ?? "";
  if (/^\.env\.(example|exemplo|sample|template)$/i.test(name)) return false;
  return /^\.env(\..*)?$/.test(name) || /\.(pem|key|p12|pfx)$/i.test(name) || /^id_(rsa|ed25519|ecdsa)$/.test(name);
}

function git(args) {
  return execFileSync("git", args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
}

function scanStaged() {
  const files = git(["diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z"]).split("\0").filter(Boolean);
  const problems = [];
  for (const file of files) {
    if (isForbiddenPath(file)) {
      problems.push(`${file}: arquivo que nunca vai pro repositório`);
      continue;
    }
    let text;
    try {
      text = git(["show", `:${file}`]);
    } catch {
      continue; // submódulo ou arquivo que sumiu do índice
    }
    if (text.includes("\0")) continue; // binário
    for (const hit of findSecrets(text)) problems.push(`${file}:${hit.line}: ${hit.kind}`);
  }
  return problems;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const problems = scanStaged();
  if (problems.length > 0) {
    console.error("[secret-scan] commit recusado, possível segredo:");
    for (const p of problems) console.error(`  ${p}`);
    console.error("Tire do índice (git restore --staged <arquivo>), mova o valor pro .env e,");
    console.error("se a chave já saiu daqui antes, troque-a no painel do serviço.");
    process.exit(1);
  }
}
