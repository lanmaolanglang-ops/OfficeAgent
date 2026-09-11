import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/manrope'
import '@fontsource-variable/newsreader'
import '@fontsource-variable/noto-serif-sc'
import './index.css'
import App from './App.tsx'
import ErrorBoundary from './components/Common/ErrorBoundary.tsx'

// ErrorBoundary 挂在 React root 的最外层：App 及其整棵子树（Router / Store /
// 各页面）在 render 与生命周期中抛出的异常都会落到这里，而不是整页白屏。
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
