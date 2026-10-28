import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] space-y-4 text-center px-4">
      <div className="text-6xl">🔍</div>
      <h2 className="text-xl font-semibold text-bone">Page not found</h2>
      <p className="text-bone-faint text-sm">
        The page you&apos;re looking for doesn&apos;t exist.
      </p>
      <Link
        href="/"
        className="text-sm text-gold-400 hover:text-gold-300 border border-gold-700/60 hover:border-gold-600 px-4 py-2 rounded-lg transition-colors"
      >
        Go home
      </Link>
    </div>
  );
}
