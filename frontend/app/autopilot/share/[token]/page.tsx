'use client';
import { useParams } from 'next/navigation';
import InvestigationView from '@/components/autopilot/InvestigationView';
export default function Page() { const { token } = useParams<{ token: string }>(); return <InvestigationView shareToken={token} />; }
