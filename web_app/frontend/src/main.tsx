import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { App } from './app/App';
import { ToastProvider } from './contexts/ToastContext';
import { i18nReady } from './i18n';
import './index.css';
import { installChunkReloadGuard } from './lib/chunkReload';

import './motion.css';

installChunkReloadGuard();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false
    }
  }
});

void i18nReady.then(() => ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* тосты внутри роутера: их кнопки ведут клиентским переходом (useNavigate) */}
      <BrowserRouter>
        <ToastProvider>
          <App />
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
));
