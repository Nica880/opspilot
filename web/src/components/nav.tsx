import Link from "next/link";

const LINKS = [
  { href: "/", label: "Inbox" },
  { href: "/approvals", label: "Approvals" },
  { href: "/search", label: "Search" },
  { href: "/evals", label: "Evals" },
  { href: "/ops", label: "Ops" },
] as const;

export function Nav() {
  return (
    <header className="border-b">
      <nav className="mx-auto flex max-w-6xl items-center gap-6 px-6 py-3 text-sm">
        <Link href="/" className="font-semibold">
          OpsPilot
        </Link>
        {LINKS.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className="text-muted-foreground hover:text-foreground"
          >
            {link.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
