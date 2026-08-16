import { HashRouter, Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import MainLayout from './components/Layout/MainLayout';

// 懒加载页面组件，减少初始bundle大小
const Dashboard = lazy(() => import('./pages/Dashboard'));
const WordAgentPage = lazy(() => import('./pages/WordAgent'));
const PPTAgentPage = lazy(() => import('./pages/PPTAgent'));
const ExcelAgentPage = lazy(() => import('./pages/ExcelAgent'));
const TaskHistory = lazy(() => import('./pages/TaskHistory'));
const FileManagerPage = lazy(() => import('./pages/FileManager'));
const SettingsPage = lazy(() => import('./pages/Settings'));

// 加载占位组件
function PageLoader() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-gray-200 border-t-blue-500"></div>
        <p className="text-sm text-gray-400">加载中...</p>
      </div>
    </div>
  );
}

function App() {
  return (
    <HashRouter>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route element={<MainLayout />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/word" element={<WordAgentPage />} />
            <Route path="/ppt" element={<PPTAgentPage />} />
            <Route path="/excel" element={<ExcelAgentPage />} />
            <Route path="/history" element={<TaskHistory />} />
            <Route path="/files" element={<FileManagerPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </HashRouter>
  );
}

export default App;
