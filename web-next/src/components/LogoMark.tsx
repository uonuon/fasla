/** Fasla's mark: a clothes hanger with a rising demand line inside it. */
export default function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg className="logo-mark" width={size} height={size} viewBox="0 0 160 160" aria-hidden="true">
      <rect width="160" height="160" rx="36" fill="#0F5E52" />
      <path d="M80 40 a10 10 0 1 1 10 10 v8" fill="none" stroke="#FFFFFF" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M90 58 L130 112 H30 Z" fill="none" stroke="#FFFFFF" strokeWidth="9" strokeLinejoin="round" />
      <path d="M38 104 L62 96 L82 100 L104 84 L124 78" fill="none" stroke="#C6F36B" strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
