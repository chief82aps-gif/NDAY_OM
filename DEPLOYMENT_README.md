# Deployment Files for NDAY OM

This directory contains all configuration needed to deploy to production.

## Files Generated for Deployment

### Backend (FastAPI on Railway)
- **Procfile** - Defines how to run the app on Railway
- **runtime.txt** - Specifies Python 3.11
- **requirements.txt** - All Python dependencies

### Frontend (Next.js on Vercel)
- **frontend/.env.local.example** - Environment template
- **frontend/package.json** - Dependencies and build config
- **next.config.ts** - Next.js configuration

## Quick Start

### 1. Push to GitHub
```bash
git add -A
git commit -m "Ready for production"
git push origin master
```

### 2. Deploy Backend to Railway (5 min)
1. Go to https://railway.app
2. Click "New Project" → "Deploy from GitHub"
3. Select your repo
4. Railway auto-detects Procfile and deploys
5. Copy the generated URL (e.g., `https://app-name.railway.app`)

### 3. Deploy Frontend to Vercel (5 min)
1. Go to https://vercel.app
2. Click "New Project" → Import Git Repo
3. Set Root Directory to `frontend`
4. Add environment variable:
   ```
   NEXT_PUBLIC_API_URL = https://app-name.railway.app
   ```
5. Deploy

### 4. Add Custom Domain (2 min)
1. In Vercel: Settings → Domains → Add `newdaylogisticsllc.com`
2. Update DNS at your registrar:
   ```
   Type: CNAME
   Name: @
   Value: cname.vercel-dns.com.
   ```
3. Wait 5-10 minutes for SSL

## Cost
- Railway: ~$5-20/month (pay-as-you-go)
- Vercel: Free
- Domain: $12/year
- **Total: $60-250/year**

## Monitoring
- Railway Dashboard: See logs, CPU, memory
- Vercel Dashboard: See deployments, errors, analytics
- Browser DevTools: Check API responses

## Troubleshooting
See DEPLOYMENT.md for detailed troubleshooting

## Security Notes
- Railway: Free HTTPS on all apps
- Vercel: Free HTTPS and auto-renewal
- CORS: Configured to accept requests from Vercel domain
- File uploads: Limited to 512MB on Railway (use S3 for scaling)

Ready to deploy? Start with `bash DEPLOY.sh` or follow the steps above!
