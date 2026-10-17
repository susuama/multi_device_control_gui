import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import DesignDemo from './DesignDemo';
import './design-demo.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode><DesignDemo /></StrictMode>,
);
