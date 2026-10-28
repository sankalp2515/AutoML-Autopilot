'use client';
import { useState } from 'react';
import type { Artifact } from '@/lib/types';
import { artifactUrl, fetchArtifactText } from '@/lib/api';
import { Button, Card } from '@/components/ui';

const ARTIFACT_META: Record<
  string,
  { icon: string; label: string; description: string; preview?: 'image' | 'text' | 'markdown' }
> = {
  notebook: { icon: '📓', label: 'Evidence Notebook', description: 'Jupyter notebook with LLM-generated narrative and executed code' },
  model_card: { icon: '🪪', label: 'Model Card', description: 'Model performance, limitations, and usage guide', preview: 'markdown' },
  api: { icon: '🌐', label: 'FastAPI Server', description: 'Ready-to-deploy /predict endpoint for this model', preview: 'text' },
  pipeline: { icon: '⚙️', label: 'Inference Pipeline', description: 'Preprocessor + model bundle (joblib dict)' },
  model: { icon: '🤖', label: 'Tuned Model', description: 'Best tuned sklearn/XGBoost model' },
  preprocessor: { icon: '🔄', label: 'Preprocessor', description: 'Fitted sklearn ColumnTransformer' },
  shap: { icon: '📊', label: 'SHAP Summary', description: 'Feature importance via SHAP values', preview: 'image' },
  confusion_matrix: { icon: '🟦', label: 'Confusion Matrix', description: 'Classification confusion matrix heatmap', preview: 'image' },
  target_dist: { icon: '📉', label: 'Target Distribution', description: 'Distribution of the target variable', preview: 'image' },
  correlation: { icon: '🗺️', label: 'Correlation Heatmap', description: 'Feature correlation heatmap (top 15 features)', preview: 'image' },
};

function MarkdownRenderer({ text }: { text: string }) {
  return (
    <pre className="text-xs text-bone-dim whitespace-pre-wrap font-mono leading-relaxed">{text}</pre>
  );
}

function ArtifactCard({ artifact, runId }: { artifact: Artifact; runId: string }) {
  const meta = ARTIFACT_META[artifact.name] ?? { icon: '📄', label: artifact.name, description: artifact.filename };
  const [expanded, setExpanded] = useState(false);
  const [previewContent, setPreviewContent] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const downloadUrl = artifactUrl(runId, artifact.name);
  const isImage = meta.preview === 'image';
  const isText = meta.preview === 'text' || meta.preview === 'markdown';

  const loadPreview = async () => {
    if (previewContent !== null || !isText) { setExpanded(!expanded); return; }
    setLoading(true);
    try {
      setPreviewContent(await fetchArtifactText(runId, artifact.name));
    } catch { /* preview is best-effort */ }
    setLoading(false);
    setExpanded(true);
  };

  return (
    <Card padding={0} delay={false} style={{ overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: 16 }}>
        <div aria-hidden="true" style={{ fontSize: 24, flexShrink: 0 }}>{meta.icon}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>{meta.label}</p>
          <p style={{ fontSize: 12, color: 'var(--t3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{meta.description}</p>
          <p style={{ fontSize: 11, color: 'var(--t4)', marginTop: 2 }}>{artifact.size_kb} KB</p>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          {(isText || isImage) && (
            <Button
              variant="ghost"
              size="sm"
              loading={loading}
              onClick={isImage ? () => setExpanded(!expanded) : loadPreview}
              aria-expanded={expanded}
            >
              {expanded ? 'Hide' : 'Preview'}
            </Button>
          )}
          <a href={downloadUrl} download={artifact.filename} className="ds-ghost" style={{ height: 32, padding: '0 14px', fontSize: 12 }}>
            Download
          </a>
        </div>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--bd)' }}>
          {isImage && (
            <div style={{ padding: 16, background: 'rgba(0,0,0,0.2)' }}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={downloadUrl} alt={`${meta.label} visualization`} style={{ maxWidth: '100%', margin: '0 auto', borderRadius: 6, display: 'block' }} />
            </div>
          )}
          {isText && previewContent !== null && (
            <div style={{ padding: 16, background: 'rgba(0,0,0,0.2)', maxHeight: 384, overflowY: 'auto' }}>
              <MarkdownRenderer text={previewContent} />
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <h4 className="ds-mono" style={{ fontSize: 11, fontWeight: 600, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 12 }}>{children}</h4>;
}

export default function ArtifactsPanel({ artifacts, runId }: { artifacts: Artifact[]; runId: string }) {
  const images = artifacts.filter((a) => ARTIFACT_META[a.name]?.preview === 'image');
  const files = artifacts.filter((a) => ARTIFACT_META[a.name]?.preview !== 'image');

  if (artifacts.length === 0) {
    return (
      <Card padding={40} style={{ textAlign: 'center' }}>
        <div aria-hidden="true" style={{ fontSize: 28, opacity: 0.6, marginBottom: 8 }}>📭</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>No artifacts yet</div>
        <p style={{ fontSize: 12.5, color: 'var(--t2)', marginTop: 4 }}>Artifacts appear here once the pipeline produces its model, notebook, and plots.</p>
      </Card>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      {files.length > 0 && (
        <div>
          <SectionLabel>Output Files</SectionLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {files.map((a) => <ArtifactCard key={a.name} artifact={a} runId={runId} />)}
          </div>
        </div>
      )}

      {images.length > 0 && (
        <div>
          <SectionLabel>Visualizations</SectionLabel>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 12 }}>
            {images.map((a) => <ArtifactCard key={a.name} artifact={a} runId={runId} />)}
          </div>
        </div>
      )}
    </div>
  );
}
