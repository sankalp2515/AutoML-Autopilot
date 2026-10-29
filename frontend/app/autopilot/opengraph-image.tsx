import { ImageResponse } from 'next/og';
export const runtime = 'edge';
export const alt = 'AutoML Autopilot: investigate, test cheaply, verify independently';
export const size = { width: 1200, height: 630 };
export const contentType = 'image/png';
export default function Image() {
  return new ImageResponse(<div style={{ width: '100%', height: '100%', display: 'flex', flexDirection: 'column', padding: 72, background: '#EAE6DC', color: '#1C1A14', fontFamily: 'sans-serif' }}>
    <div style={{ color: '#0B6E74', fontSize: 30, fontWeight: 700, display: 'flex' }}>↗ AutoML Autopilot</div>
    <div style={{ fontSize: 66, fontWeight: 700, marginTop: 50, lineHeight: 1.1, display: 'flex' }}>When a model breaks, prove the fix.</div>
    <div style={{ marginTop: 48, fontSize: 28, display: 'flex' }}>Investigate → Test cheaply → Verify independently</div>
    <div style={{ marginTop: 36, fontSize: 22, color: '#5C574A', display: 'flex' }}>NVIDIA Nemotron · Nebius · Tavily</div>
  </div>, size);
}
