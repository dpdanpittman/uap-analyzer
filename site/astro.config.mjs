import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind';

// Deployment target: uap.mabus.ai via Caddy → k8s nginx pod (hostPort 3404)
// See site/k8s/uap-website.yaml for the deployment manifest.
export default defineConfig({
  integrations: [tailwind()],
  site: 'https://uap.mabus.ai',
});
