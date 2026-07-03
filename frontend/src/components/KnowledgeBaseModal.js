import React, { useState, useRef, useEffect, useCallback } from 'react';
import './KnowledgeBaseModal.css';

const API_BASE = 'https://6que5dlvtc.execute-api.us-east-1.amazonaws.com/prod';

/* ─── Chunk type config ─────────────────────────────────────────── */
const CHUNK_TYPE_META = {
  Text:  { label: 'Text',  color: '#60a5fa', bg: 'rgba(96,165,250,0.12)',  border: 'rgba(96,165,250,0.25)' },
  Table: { label: 'Table', color: '#fb923c', bg: 'rgba(251,146,60,0.12)',  border: 'rgba(251,146,60,0.25)' },
  Image: { label: 'Image', color: '#c084fc', bg: 'rgba(192,132,252,0.12)', border: 'rgba(192,132,252,0.25)' },
};


/* ─── Fetch all documents ───────────────────────────────────────── */
async function fetchDocuments() {
  const res  = await fetch(`${API_BASE}/documents`);
  const data = await res.json();
  console.log('[KB] fetchDocuments raw response:', data);
  return data.documents || [];
}

async function fetchChunks(documentId) {
  const url = `${API_BASE}/chunks?documentId=${encodeURIComponent(documentId)}`;
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Server error ${res.status} on /chunks`);
  const data = await res.json();
  console.log('[KB] fetchChunks raw response:', data);
  return data.chunks || data.items || data.data || data.chunkList || [];
}

/* ─── Upload a file ─────────────────────────────────────────────── */
async function uploadFile(file, category) {
  // Step 1: Get presigned URL (no signing needed — auth is NONE)
  const urlRes = await fetch(`${API_BASE}/upload`, {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({
      filename:    file.name,
      category:    category || 'general',
      source_type: 'community'
    })
  });

  if (!urlRes.ok) {
    const err = await urlRes.json().catch(() => ({}));
    throw new Error(err.error || `HTTP ${urlRes.status}`);
  }

  const { uploadUrl, documentId } = await urlRes.json();

  // Step 2: PUT directly to S3 presigned URL (no auth header needed)
  const s3Res = await fetch(uploadUrl, {
    method:  'PUT',
    headers: { 'Content-Type': file.type || 'application/octet-stream' },
    body:    file
  });

  if (!s3Res.ok) {
    throw new Error(`S3 upload failed: ${s3Res.status}`);
  }

  return { documentId };
}

/* ─── ChunkRow ──────────────────────────────────────────────────── */
function ChunkRow({ chunk }) {
  const [expanded, setExpanded] = useState(false);
  const meta    = CHUNK_TYPE_META[chunk.chunkType] || CHUNK_TYPE_META.Text;
  const preview = chunk.content.slice(0, 150);
  const hasMore = chunk.content.length > 150;
  return (
    <div className="kb-chunk-row">
      <div className="kb-chunk-row-header">
        <span className="kb-chunk-type-badge" style={{ color: meta.color, background: meta.bg, borderColor: meta.border }}>
          {meta.label}
        </span>
        <span className="kb-chunk-source">📄 {chunk.sourceFile}</span>
      </div>
      <div className="kb-chunk-content">
        {expanded ? chunk.content : preview}{!expanded && hasMore && '…'}
      </div>
      {hasMore && (
        <button className="kb-show-more-btn" onClick={() => setExpanded(e => !e)}>
          {expanded ? 'Show less ▲' : 'Show more ▼'}
        </button>
      )}
    </div>
  );
}

/* ─── DocumentRow ───────────────────────────────────────────────── */
function DocumentRow({ doc, expandedDoc, chunkMap, chunkErrors, chunksLoading, onViewChunks }) {
  const isOpen    = expandedDoc === doc.documentId;
  const chunks    = chunkMap[doc.documentId];
  const chunkErr  = chunkErrors[doc.documentId];

  return (
    <div className={`kb-doc-row ${isOpen ? 'kb-doc-row--open' : ''}`}>
      <div className="kb-doc-row-main">
        <div className="kb-doc-info">
          <div className="kb-doc-filename">📄 {doc.filename}</div>
          <div className="kb-doc-meta">
            <span className="kb-doc-chunks">{doc.chunkCount} chunks</span>
            <span className={`kb-doc-status kb-doc-status--${doc.status.toLowerCase()}`}>
              {doc.status === 'Indexed' ? '✓' : '⏳'} {doc.status}
            </span>
          </div>
        </div>
        <button
          className={`kb-view-chunks-btn ${isOpen ? 'active' : ''}`}
          onClick={() => onViewChunks(doc.documentId)}
          disabled={chunksLoading && !isOpen}
        >
          {chunksLoading && isOpen
            ? <span className="kb-mini-spinner" />
            : isOpen ? 'Hide ▲' : 'View ▼'}
        </button>
      </div>

      {isOpen && (
        <div className="kb-chunks-panel">
          {chunkErr ? (
            <div className="kb-empty-chunks" style={{ color: '#f87171' }}>
              ⚠️ {chunkErr}
            </div>
          ) : !chunks ? (
            <div className="kb-empty-chunks"><span className="kb-mini-spinner" /> Loading...</div>
          ) : chunks.length === 0 ? (
            <div className="kb-empty-chunks">No chunks available.</div>
          ) : (
            chunks.map(c => <ChunkRow key={c.chunkId} chunk={c} />)
          )}
        </div>
      )}
    </div>
  );
}

/* ─── UploadTab ─────────────────────────────────────────────────── */
function UploadTab({ selectedFile, setSelectedFile, uploadStatus, onUpload }) {
  const [dragOver,   setDragOver]   = useState(false);
  const [fileError,  setFileError]  = useState(null);
  const fileInputRef = useRef(null);

  const validateFile = (f) => {
    if (!['application/pdf', 'text/plain'].includes(f.type) && !f.name.match(/\.(pdf|txt)$/i))
      return 'Only PDF and TXT files are supported.';
    if (f.size > 10 * 1024 * 1024)
      return 'File exceeds the 10 MB limit.';
    return null;
  };

  const applyFile = (f) => {
    const err = validateFile(f);
    if (err) { setFileError(err); return; }
    setFileError(null);
    setSelectedFile(f);
  };

  const handleDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    const f = e.dataTransfer.files[0];
    if (f) applyFile(f);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleFileInput = (e) => {
    const f = e.target.files[0];
    if (f) applyFile(f);
  };

  const busy      = uploadStatus === 'uploading' || uploadStatus === 'processing';
  const canUpload = selectedFile && !busy;

  return (
    <div className="kb-tab-content">
      <div className="kb-section-label">Add Documents</div>

      <div
        className={`kb-dropzone ${dragOver ? 'kb-dropzone--over' : ''} ${selectedFile ? 'kb-dropzone--has-file' : ''}`}
        onDragOver={e => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        role="button" tabIndex={0}
        onKeyDown={e => e.key === 'Enter' && fileInputRef.current?.click()}
        aria-label="Upload file drop zone"
      >
        <input
          ref={fileInputRef}
          id="kb-file-input"
          type="file"
          accept=".pdf,.txt"
          style={{ display: 'none' }}
          onChange={handleFileInput}
        />
        <div className="kb-dropzone-icon">{selectedFile ? '📄' : '⬆'}</div>
        {selectedFile ? (
          <>
            <div className="kb-dropzone-filename">{selectedFile.name}</div>
            <div className="kb-dropzone-sub">{(selectedFile.size / 1024).toFixed(1)} KB · click to change</div>
          </>
        ) : (
          <>
            <div className="kb-dropzone-title">Drop files or click to upload</div>
            <div className="kb-dropzone-sub">PDF, TXT &bull; Max 10 MB</div>
          </>
        )}
      </div>

      {fileError && <div className="kb-status kb-status--error">{fileError}</div>}

      <button id="kb-upload-btn" className="kb-upload-btn" onClick={onUpload} disabled={!canUpload}>
        {uploadStatus === 'uploading'  && <><span className="kb-mini-spinner" /> Uploading...</>}
        {uploadStatus === 'processing' && <><span className="kb-mini-spinner" /> Processing...</>}
        {(!uploadStatus || uploadStatus === 'error' || uploadStatus === 'indexed') && 'Upload Document'}
      </button>

      {uploadStatus === 'indexed' && (
        <div className="kb-status kb-status--success">Indexed successfully. Switching to Documents...</div>
      )}
      {uploadStatus === 'error' && (
        <div className="kb-status kb-status--error">Upload failed. Please try again.</div>
      )}

      <div className="kb-upload-tip">
        Once indexed, the AI assistant will use your document when answering questions.
      </div>
    </div>
  );
}

/* ─── DocumentsTab ──────────────────────────────────────────────── */
function DocumentsTab({ documents, docsLoading, expandedDoc, chunkMap, chunkErrors, chunksLoading, onViewChunks }) {
  if (docsLoading) return (
    <div className="kb-tab-content kb-state-center">
      <div className="kb-spinner" /><span>Loading documents...</span>
    </div>
  );

  if (!documents || documents.length === 0) return (
    <div className="kb-tab-content kb-state-center">
      <div className="kb-state-icon">📂</div>
      <div className="kb-empty-title">No documents yet</div>
      <div className="kb-empty-sub">Switch to Upload to add your first document.</div>
    </div>
  );

  return (
    <div className="kb-tab-content">
      <div className="kb-section-label">
        {documents.length} document{documents.length !== 1 ? 's' : ''} indexed
      </div>
      <div className="kb-docs-list">
        {documents.map(doc => (
          <DocumentRow
            key={doc.documentId}
            doc={doc}
            expandedDoc={expandedDoc}
            chunkMap={chunkMap}
            chunkErrors={chunkErrors}
            chunksLoading={chunksLoading}
            onViewChunks={onViewChunks}
          />
        ))}
      </div>
    </div>
  );
}

/* ─── Main Panel ────────────────────────────────────────────────── */
export default function KnowledgeBaseModal() {
  const [isCollapsed,    setIsCollapsed]    = useState(false);
  const [activeTab,      setActiveTab]      = useState('upload');

  // Documents state
  const [documents,      setDocuments]      = useState([]);
  const [docsLoading,    setDocsLoading]    = useState(false);

  // Upload state
  const [selectedFile,   setSelectedFile]   = useState(null);
  const [uploadStatus,   setUploadStatus]   = useState(null); // 'uploading'|'processing'|'indexed'|'error'|null

  // Chunks state
  const [expandedDoc,    setExpandedDoc]    = useState(null);
  const [chunkMap,       setChunkMap]       = useState({});
  const [chunkErrors,    setChunkErrors]    = useState({});
  const [chunksLoading,  setChunksLoading]  = useState(false);

  // ── Fetch documents when documents tab is active ───────────────
  useEffect(() => {
    if (activeTab === 'documents') {
      setDocsLoading(true);
      fetchDocuments()
        .then(docs => setDocuments(docs))
        .catch(err  => console.error('Failed to load documents:', err))
        .finally(()  => setDocsLoading(false));
    }
  }, [activeTab]);

  // ── Upload handler ─────────────────────────────────────────────
  async function handleUpload() {
    if (!selectedFile) return;
    setUploadStatus('uploading');
    try {
      setUploadStatus('processing');
      await uploadFile(selectedFile);
      setUploadStatus('indexed');
      setTimeout(() => {
        setActiveTab('documents');
        setUploadStatus(null);
        setSelectedFile(null);
      }, 1500);
    } catch (err) {
      console.error('Upload failed:', err);
      setUploadStatus('error');
    }
  }

  // ── Chunk viewer handler ───────────────────────────────────────
  async function handleViewChunks(documentId) {
    if (expandedDoc === documentId) {
      setExpandedDoc(null);
      return;
    }
    setExpandedDoc(documentId);
    if (chunkMap[documentId]) return; // already loaded
    setChunksLoading(true);
    try {
      const chunks = await fetchChunks(documentId);
      setChunkMap(prev => ({ ...prev, [documentId]: chunks }));
      setChunkErrors(prev => { const n = { ...prev }; delete n[documentId]; return n; });
    } catch (err) {
      console.error('Failed to load chunks:', err);
      setChunkErrors(prev => ({ ...prev, [documentId]: err.message }));
    } finally {
      setChunksLoading(false);
    }
  }

  return (
    <aside className={`kb-panel ${isCollapsed ? 'kb-panel--collapsed' : ''}`} aria-label="Knowledge Base">

      {/* Collapse / expand toggle strip */}
      <div
        className="kb-panel-tab"
        onClick={() => setIsCollapsed(c => !c)}
        title={isCollapsed ? 'Open Knowledge Base' : 'Collapse'}
        role="button" tabIndex={0}
        onKeyDown={e => e.key === 'Enter' && setIsCollapsed(c => !c)}
        aria-label={isCollapsed ? 'Open Knowledge Base' : 'Collapse Knowledge Base'}
      >
        <span className="kb-panel-tab-chevron">{isCollapsed ? '‹' : '›'}</span>
        {isCollapsed && <span className="kb-panel-tab-icon">📁</span>}
      </div>

      {/* Panel content */}
      <div className="kb-panel-inner">

        {/* Header */}
        <div className="kb-panel-header">
          <div className="kb-panel-title">
            <div className="kb-panel-icon">📁</div>
            <div>
              <div className="kb-panel-title-text">Knowledge Base</div>
              <div className="kb-panel-title-sub">Your uploaded documents</div>
            </div>
          </div>
          <button
            className="kb-add-btn"
            onClick={() => setActiveTab('upload')}
            aria-label="Add document"
            title="Add document"
          >+</button>
        </div>

        {/* Tabs */}
        <div className="kb-tabs" role="tablist">
          <button
            id="kb-tab-upload"
            className={`kb-tab ${activeTab === 'upload' ? 'kb-tab--active' : ''}`}
            onClick={() => setActiveTab('upload')}
            role="tab"
            aria-selected={activeTab === 'upload'}
          >Upload</button>
          <button
            id="kb-tab-documents"
            className={`kb-tab ${activeTab === 'documents' ? 'kb-tab--active' : ''}`}
            onClick={() => setActiveTab('documents')}
            role="tab"
            aria-selected={activeTab === 'documents'}
          >Documents</button>
        </div>

        {/* Body */}
        <div className="kb-panel-body">
          {activeTab === 'upload' && (
            <UploadTab
              selectedFile={selectedFile}
              setSelectedFile={setSelectedFile}
              uploadStatus={uploadStatus}
              onUpload={handleUpload}
            />
          )}
          {activeTab === 'documents' && (
            <DocumentsTab
              documents={documents}
              docsLoading={docsLoading}
              expandedDoc={expandedDoc}
              chunkMap={chunkMap}
              chunkErrors={chunkErrors}
              chunksLoading={chunksLoading}
              onViewChunks={handleViewChunks}
            />
          )}
        </div>
      </div>
    </aside>
  );
}
