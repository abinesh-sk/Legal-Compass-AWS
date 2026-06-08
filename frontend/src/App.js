import React, { useState, useEffect, useRef } from 'react';
import { fetchAuthSession } from 'aws-amplify/auth';
import './App.css';

const API_ENDPOINT = 'https://6que5dlvtc.execute-api.us-east-1.amazonaws.com/prod/chat';
const SESSION_ID   = 'session-' + Math.random().toString(36).substr(2, 9);
const FEEDBACK_EMAIL = 'abinesh.sk.1604@gmail.com';

/* ── Quick-ask chips shown above the input ── */
const SUGGESTIONS = [
  { text: 'Can my landlord evict me without notice?',          icon: '🏠' },
  { text: 'What are my rights as an employee?',                icon: '💼' },
  { text: 'What government welfare schemes can I apply to?',   icon: '🏛️' },
  { text: 'How do I check immigration and visa requirements?', icon: '✈️' },
];

/* ── Legal categories ── */
const CATEGORIES = [
  { id: 'tenant-rights',       icon: '🏠', name: 'Tenant Rights',       desc: 'Landlord issues, rent, eviction policies',     color: 'var(--cat-tenant)',       bg: 'var(--cat-tenant-bg)',      q: 'Can my landlord evict me without notice?' },
  { id: 'employment-rights',   icon: '💼', name: 'Employment Rights',    desc: 'Contracts, wages, workplace termination',      color: 'var(--cat-employ)',       bg: 'var(--cat-employ-bg)',      q: 'What are my rights as an employee?' },
  { id: 'government-benefits', icon: '🏛️', name: 'Government Benefits',  desc: 'Welfare schemes, eligibility, government aid', color: 'var(--cat-benefits)',     bg: 'var(--cat-benefits-bg)',    q: 'What government welfare schemes can I apply to?' },
  { id: 'immigration',         icon: '✈️', name: 'Immigration and Visas', desc: 'Visa processes, residency, legal status',     color: 'var(--cat-immigration)',  bg: 'var(--cat-immigration-bg)', q: 'How do I check immigration and visa requirements?' },
];

const getCategoryMeta = (id) => {
  const cat = CATEGORIES.find(c => c.id === id);
  return cat
    ? { label: `${cat.icon} ${cat.name}`, color: cat.color, bg: cat.bg }
    : { label: '⚖️ Legal', color: 'var(--cat-legal)', bg: 'var(--cat-legal-bg)' };
};

/* ════════════════════════════════════════════════════════════════
   CHAT PAGE
════════════════════════════════════════════════════════════════ */
/* Welcome messages shown one by one like a live chat */
const WELCOME_BUBBLES = [
  "Hey there! Welcome to Legal Compass 👋",
  "I'm here to help you make sense of your legal rights in plain, simple language. Whether you're dealing with a difficult landlord, unsure about your workplace rights, trying to figure out which government schemes you qualify for, or sorting out visa and immigration stuff - I've got you covered.",
  "Just type your question below in any language you're comfortable with and I'll do my best to help. Let's get started!",
];

function ChatPage() {
  const [messages, setMessages]         = useState([]);
  const [inputText, setInputText]       = useState('');
  const [isLoading, setIsLoading]       = useState(false);
  const [isWelcomeTyping, setIsWelcomeTyping] = useState(false);
  const [error, setError]               = useState(null);
  const endRef                           = useRef(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isWelcomeTyping]);

  /* Drip-feed the 3 welcome bubbles with typing indicators between them */
  useEffect(() => {
    const delays = [400, 1800, 3800]; // when each typing indicator starts
    const shows  = [1100, 3000, 5200]; // when each bubble actually appears

    const timers = [];

    WELCOME_BUBBLES.forEach((text, i) => {
      // Show typing indicator
      timers.push(setTimeout(() => setIsWelcomeTyping(true), delays[i]));
      // Drop in the bubble and hide typing indicator
      timers.push(setTimeout(() => {
        setMessages(prev => [
          ...prev,
          { role: 'assistant', content: text, timestamp: new Date().toISOString() },
        ]);
        // Only clear typing after last bubble
        if (i === WELCOME_BUBBLES.length - 1) setIsWelcomeTyping(false);
      }, shows[i]));
    });

    return () => timers.forEach(clearTimeout);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sendMessage = async () => {
    const message = inputText.trim();
    if (!message || isLoading) return;

    setMessages(prev => [...prev, { role: 'user', content: message, timestamp: new Date().toISOString() }]);
    setInputText('');
    setIsLoading(true);
    setError(null);

    try {
      const session     = await fetchAuthSession();
      const credentials = session.credentials;
      if (!credentials) throw new Error('Could not get AWS credentials');

      const body = JSON.stringify({ message, sessionId: SESSION_ID });

      const { SignatureV4 } = await import('@aws-sdk/signature-v4');
      const { HttpRequest } = await import('@aws-sdk/protocol-http');
      const { Sha256 }      = await import('@aws-crypto/sha256-js');

      const url     = new URL(API_ENDPOINT);
      const request = new HttpRequest({
        method: 'POST', hostname: url.hostname, path: url.pathname,
        headers: { 'Content-Type': 'application/json', host: url.hostname },
        body,
      });

      const signer = new SignatureV4({
        credentials: {
          accessKeyId:     credentials.accessKeyId,
          secretAccessKey: credentials.secretAccessKey,
          sessionToken:    credentials.sessionToken,
        },
        region: 'us-east-1', service: 'execute-api', sha256: Sha256,
      });

      const signedRequest = await signer.sign(request);
      const response = await fetch(API_ENDPOINT, {
        method: 'POST', headers: signedRequest.headers, body,
      });
      if (!response.ok) throw new Error(`HTTP error: ${response.status}`);

      const data = await response.json();
      setMessages(prev => [...prev, {
        role:      'assistant',
        content:   data.answer || "Hmm, I wasn't able to process that. Mind giving it another try?",
        sources:   data.sources  || [],
        category:  data.category || '',
        language:  data.detectedLanguage || 'en',
        timestamp: new Date().toISOString(),
      }]);
    } catch (err) {
      console.error('Error sending message:', err);
      setError('Something went wrong on our end. Please give it another try.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  return (
    <div className="chat-page">
      {/* Chat sub-header */}
      <div className="chat-subheader">
        <div className="chat-subheader-left">
          <div className="chat-bot-avatar">⚖️</div>
          <div>
            <div className="chat-title">Legal Compass Assistant</div>
            <div className="chat-subtitle">AI-powered legal guidance - responds in your language</div>
          </div>
        </div>
        <span className="nav-pill live">Online</span>
      </div>

      {/* Messages */}
      <div className="messages-container">
        {messages.map((msg, i) => {
          const catMeta = msg.category ? getCategoryMeta(msg.category) : null;
          const isWelcomeBubble = i < WELCOME_BUBBLES.length && msg.role === 'assistant';
          return (
            <div key={i} className={`message-wrapper ${msg.role}${isWelcomeBubble ? ' welcome' : ''}`}>
              <div className={`message ${msg.role}`}>
                <div className="message-content">
                  {msg.content.split('\n').map((line, j) => <p key={j}>{line}</p>)}
                </div>
                {(catMeta || (msg.sources && msg.sources.length > 0)) && (
                  <div className="message-meta">
                    {catMeta && (
                      <span className="category-badge" style={{ background: catMeta.bg, color: catMeta.color }}>
                        {catMeta.label}
                      </span>
                    )}
                    {msg.sources && msg.sources.length > 0 && (
                      <>
                        <div className="sources-label">Reference Sources</div>
                        <div className="sources-list">
                          {msg.sources.map((src, si) => (
                            <span key={si} className="source-pill">📄 {src}</span>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {/* Welcome typing indicator - shows between each welcome bubble */}
        {isWelcomeTyping && (
          <div className="message-wrapper assistant">
            <div className="message assistant">
              <div className="typing-indicator"><span /><span /><span /></div>
            </div>
          </div>
        )}

        {/* Regular response typing indicator */}
        {isLoading && (
          <div className="message-wrapper assistant">
            <div className="message assistant">
              <div className="typing-indicator"><span /><span /><span /></div>
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Quick chips */}
      <div className="chips-wrapper">
        {SUGGESTIONS.map((chip, idx) => (
          <div key={idx} className="chip" onClick={() => setInputText(chip.text)}>
            {chip.icon} {chip.text}
          </div>
        ))}
      </div>

      {/* Input */}
      <div className="input-area">
        <div className="input-container">
          <textarea
            id="chat-input"
            className="input-box"
            value={inputText}
            onChange={e => setInputText(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask a legal question in any language... (Press Enter to send)"
            rows={1}
            disabled={isLoading}
          />
          <button
            id="send-btn"
            className="send-btn"
            onClick={sendMessage}
            disabled={isLoading || !inputText.trim()}
          >
            {isLoading ? <div className="btn-spinner" /> : '➤'}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════
   ABOUT PAGE
════════════════════════════════════════════════════════════════ */
function AboutPage() {
  const cards = [
    {
      icon: '⚖️', color: 'var(--cat-legal)', bg: 'var(--cat-legal-bg)',
      title: 'What is Legal Compass?',
      text: "Legal Compass is a free AI assistant that helps everyday people understand their legal rights without the jargon. It was built because legal information should be accessible to everyone, not just those who can afford a lawyer.",
    },
    {
      icon: '🧠', color: 'var(--accent-purple)', bg: 'rgba(168,85,247,0.12)',
      title: 'How does it work?',
      text: "It uses Retrieval-Augmented Generation (RAG) - a fancy way of saying it searches through trusted legal documents and then gives you a clear, plain-language answer tailored to your question. Think of it as a very well-read legal assistant.",
    },
    {
      icon: '🌐', color: 'var(--cat-employ)', bg: 'var(--cat-employ-bg)',
      title: 'Multilingual Support',
      text: "You can ask your question in English, Tamil, Hindi, or pretty much any language you prefer. Legal Compass will detect your language and respond in kind, making it genuinely useful for people from all backgrounds.",
    },
    {
      icon: '🔒', color: 'var(--cat-immigration)', bg: 'var(--cat-immigration-bg)',
      title: 'Anonymous and Secure',
      text: "No login required, no data stored about you. Every session is completely anonymous. You can ask sensitive legal questions without worrying about your privacy - it stays between you and the assistant.",
    },
    {
      icon: '🏠', color: 'var(--cat-tenant)', bg: 'var(--cat-tenant-bg)',
      title: 'What topics does it cover?',
      text: "Right now, it covers tenant and landlord disputes, employment and workplace rights, government welfare schemes, and immigration and visa matters. More areas will be added as the project grows.",
    },
    {
      icon: '💡', color: 'var(--accent)', bg: 'rgba(99,102,241,0.12)',
      title: 'Is this legal advice?',
      text: "No - and that's important to be clear about. Legal Compass gives you general legal information to help you understand your situation better. For advice specific to your case, please talk to a qualified lawyer.",
    },
  ];

  const awsServices = [
    { icon: '🔐', name: 'Amazon Cognito',   desc: 'Handles anonymous session authentication so you never need to create an account or log in.' },
    { icon: '🚪', name: 'Amazon API Gateway', desc: 'Securely routes every chat request to the backend, with AWS Signature V4 signing on each call.' },
    { icon: '⚡', name: 'AWS Lambda',        desc: 'Runs the serverless backend logic that processes your questions and queries the knowledge base.' },
    { icon: '🤖', name: 'Amazon Bedrock',    desc: 'Powers the AI responses using foundation models, generating accurate and context-aware legal answers.' },
    { icon: '📚', name: 'Amazon Kendra',     desc: 'The RAG retrieval engine that searches through trusted legal documents to ground every answer in real sources.' },
    { icon: '🗄️', name: 'Amazon S3',         desc: 'Stores the legal document corpus that the AI searches through when answering your questions.' },
  ];

  return (
    <div className="scroll-page">
      <div className="page-hero">
        <div className="page-hero-icon">⚖️</div>
        <h1>About Legal Compass</h1>
        <p>
          Legal Compass started with a simple idea - everyone deserves to understand their rights,
          regardless of their background, income, or education level. So we built an AI assistant
          that explains complex legal concepts in plain, everyday language. No legalese, no confusion.
        </p>
      </div>
      <div className="page-cards">
        {cards.map((card, i) => (
          <div className="info-card" key={i}>
            <div className="info-card-icon" style={{ background: card.bg, color: card.color }}>
              {card.icon}
            </div>
            <h3>{card.title}</h3>
            <p>{card.text}</p>
          </div>
        ))}
      </div>

      {/* AWS Tech Stack Section */}
      <div className="aws-section">
        <div className="aws-section-inner">
          <div className="aws-section-header">
            <div className="aws-logo-badge">
              <span className="aws-logo-text">AWS</span>
            </div>
            <div>
              <h2 className="aws-section-title">Fully Built on AWS</h2>
              <p className="aws-section-sub">
                Every part of Legal Compass runs on Amazon Web Services. Here's a look at the
                services that power the experience from your browser all the way to the AI.
              </p>
            </div>
          </div>
          <div className="aws-cards-grid">
            {awsServices.map((svc, i) => (
              <div className="aws-card" key={i}>
                <div className="aws-card-icon">{svc.icon}</div>
                <div>
                  <div className="aws-card-name">{svc.name}</div>
                  <div className="aws-card-desc">{svc.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════
   HOW TO USE PAGE
════════════════════════════════════════════════════════════════ */
function HowToUsePage() {
  const steps = [
    {
      title: 'Pick your topic or just start typing',
      text: "You can click on one of the suggested question chips above the input box to get started quickly, or just type your own question directly. There is no wrong way to begin.",
    },
    {
      title: 'Ask in any language you are comfortable with',
      text: "You do not have to write in English. Type in Tamil, Hindi, or any other language and Legal Compass will understand and respond in the same language you used.",
    },
    {
      title: 'Read the response and check the sources',
      text: "The assistant will give you a clear, plain-language answer. If it references specific laws or documents, you will see them listed below the response as source pills so you know exactly where the information comes from.",
    },
    {
      title: 'Use the category cards for focused help',
      text: "Not sure how to phrase your question? Try clicking on one of the legal categories in the sidebar. Each card has a preloaded question to get the conversation going in the right direction.",
    },
    {
      title: 'Keep asking follow-up questions',
      text: "The assistant remembers your conversation within the same session. So feel free to ask follow-up questions, request clarification, or dig deeper into any part of the response.",
    },
    {
      title: 'Consult a lawyer for your specific situation',
      text: "Legal Compass gives you a solid starting point and helps you understand the general picture. But if your situation is complex or high-stakes, please reach out to a qualified lawyer who can give you advice specific to your case.",
    },
  ];

  const tips = [
    { icon: '✅', text: 'Be specific about your situation for better answers' },
    { icon: '🌍', text: 'Works in English, Tamil, Hindi and more' },
    { icon: '📄', text: 'Check the source documents for verified info' },
    { icon: '🔄', text: 'Ask follow-ups freely in the same session' },
    { icon: '🔒', text: 'Completely anonymous - no login needed' },
    { icon: '⚡', text: 'Most questions get answered in a few seconds' },
  ];

  return (
    <div className="scroll-page">
      <div className="page-hero">
        <div className="page-hero-icon">📖</div>
        <h1>How to Use Legal Compass</h1>
        <p>
          Using Legal Compass is pretty straightforward. Here is a quick walkthrough
          to help you get the most out of every conversation.
        </p>
      </div>

      <div className="steps-container">
        {steps.map((step, i) => (
          <div className="step-card" key={i}>
            <div className="step-num-badge">{i + 1}</div>
            <div className="step-content">
              <h4>{step.title}</h4>
              <p>{step.text}</p>
            </div>
          </div>
        ))}

        <div className="tips-section">
          <h2>Quick Tips</h2>
          <div className="tips-grid">
            {tips.map((tip, i) => (
              <div className="tip-chip" key={i}>
                <span>{tip.icon}</span> {tip.text}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════
   SUGGESTIONS / FEEDBACK PAGE
════════════════════════════════════════════════════════════════ */
function SuggestionsPage() {
  const [form, setForm]       = useState({ name: '', email: '', message: '' });
  const [status, setStatus]   = useState(null); // 'sending' | 'success' | 'error'

  const handleChange = (e) => setForm(prev => ({ ...prev, [e.target.name]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!form.name.trim() || !form.email.trim() || !form.message.trim()) {
      setStatus({ type: 'error', text: 'Please fill in all fields before sending.' });
      return;
    }
    setStatus({ type: 'sending', text: 'Sending your message...' });

    try {
      const res = await fetch(`https://formsubmit.co/ajax/${FEEDBACK_EMAIL}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({
          name:    form.name,
          email:   form.email,
          message: form.message,
          _subject: `Legal Compass Feedback from ${form.name}`,
        }),
      });
      const data = await res.json();
      if (data.success === 'true' || data.success === true) {
        setStatus({ type: 'success', text: "Thanks so much for reaching out! Your message has been sent to Abinesh and he'll get back to you soon." });
        setForm({ name: '', email: '', message: '' });
      } else {
        throw new Error('Submission failed');
      }
    } catch {
      setStatus({ type: 'error', text: "Hmm, something went wrong while sending. Please try again in a moment." });
    }
  };

  return (
    <div className="suggestions-page">
      <div className="suggestions-form-wrap">
        <div className="suggestions-hero">
          <div className="suggestions-icon">💬</div>
          <h1>Suggestion and Feedback</h1>
          <p>
            Got an idea that could make Legal Compass better? Run into something that did not
            work the way you expected? We genuinely want to hear from you. Drop your thoughts
            below and let's make this tool more useful together.
          </p>
        </div>

        <form onSubmit={handleSubmit} noValidate>
          <div className="form-row">
            <div className="form-group">
              <input
                id="feedback-name"
                className="form-input"
                type="text"
                name="name"
                placeholder="Full name"
                value={form.name}
                onChange={handleChange}
                disabled={status?.type === 'sending'}
              />
            </div>
            <div className="form-group">
              <input
                id="feedback-email"
                className="form-input"
                type="email"
                name="email"
                placeholder="Email address"
                value={form.email}
                onChange={handleChange}
                disabled={status?.type === 'sending'}
              />
            </div>
          </div>

          <textarea
            id="feedback-message"
            className="form-textarea"
            name="message"
            placeholder="Your message - share any ideas, feedback, bugs, or anything on your mind..."
            value={form.message}
            onChange={handleChange}
            disabled={status?.type === 'sending'}
          />

          <button
            id="feedback-submit"
            type="submit"
            className="form-submit-btn"
            disabled={status?.type === 'sending'}
          >
            {status?.type === 'sending' ? (
              <><div className="btn-spinner" /> Sending...</>
            ) : (
              <>Send Message &#9993;</>
            )}
          </button>
        </form>

        {status && status.type !== 'sending' && (
          <div className={`form-status ${status.type}`}>
            {status.type === 'success' ? '✅' : '⚠️'} {status.text}
          </div>
        )}
      </div>
    </div>
  );
}

/* ════════════════════════════════════════════════════════════════
   ROOT APP
════════════════════════════════════════════════════════════════ */
export default function App() {
  const [page, setPage]               = useState('chat');
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  const navItems = [
    { id: 'chat',        label: 'Chat' },
    { id: 'about',       label: 'About' },
    { id: 'how-to-use',  label: 'How to Use' },
    { id: 'suggestions', label: 'Suggestions' },
  ];

  const goTo = (id) => { setPage(id); setMobileNavOpen(false); };

  const renderPage = () => {
    switch (page) {
      case 'about':       return <AboutPage />;
      case 'how-to-use':  return <HowToUsePage />;
      case 'suggestions': return <SuggestionsPage />;
      default:            return <ChatPage />;
    }
  };

  return (
    <>
      <div className="page-bg" />
      <div className="glow-orb glow-orb-1" />
      <div className="glow-orb glow-orb-2" />

      <div className="page-layout">

        {/* ── TOP NAVBAR ── */}
        <nav className="top-navbar">
          <div className="navbar-brand" onClick={() => goTo('chat')}>
            <div className="navbar-logo">⚖️</div>
            <div>
              <div className="navbar-name">Legal Compass</div>
              <div className="navbar-tagline">Legal Guidance Made Simple</div>
            </div>
          </div>

          {/* Center nav links */}
          <div className={`nav-links ${mobileNavOpen ? 'mobile-open' : ''}`}>
            {navItems.map(item => (
              <button
                key={item.id}
                className={`nav-link ${page === item.id ? 'active' : ''}`}
                onClick={() => goTo(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="navbar-right">
            <span className="nav-pill live">Anonymous</span>
            <span className="nav-pill">Free</span>
            <button
              className="mobile-menu-btn"
              onClick={() => setMobileNavOpen(o => !o)}
              aria-label="Toggle navigation"
            >
              {mobileNavOpen ? '✕' : '☰'}
            </button>
          </div>
        </nav>

        {/* ── PAGE CONTENT ── */}
        <div className="main-content">
          {renderPage()}
        </div>

        {/* ── FOOTER ── */}
        <footer className="app-footer">
          <div className="footer-left">
            &copy; 2025 <span>Legal Compass</span>. All rights reserved.
          </div>
          <div className="footer-center">
            <span className="footer-aws-badge">&#9729; Powered by AWS</span>
            &nbsp;&middot;&nbsp; General legal information only - not professional legal advice.
          </div>
          <div className="footer-right">
            Designed and Developed by <a href="#!" aria-label="Developer">Abinesh</a>
          </div>
        </footer>

      </div>
    </>
  );
}