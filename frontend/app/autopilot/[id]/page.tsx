'use client';
import { useParams } from 'next/navigation';
import InvestigationView from '@/components/autopilot/InvestigationView';
export default function Page() { const { id } = useParams<{ id: string }>(); return <InvestigationView id={id} />; }
