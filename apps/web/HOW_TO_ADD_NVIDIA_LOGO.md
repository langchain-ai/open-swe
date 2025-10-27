# How to Add NVIDIA Logo Image

## 📍 **Where to Put the Logo**

Place your NVIDIA logo image here:
```
C:\Users\idant\Code\open-swe\open-swe\apps\web\public\nvidia-logo.png
```

Or if you have SVG:
```
C:\Users\idant\Code\open-swe\open-swe\apps\web\public\nvidia-logo.svg
```

---

## 📋 **Steps:**

### 1. Copy Your Logo File

**Option A - PNG:**
```powershell
copy "C:\Users\idant\Code\BMAD\nvidia-logo-dark-1920x1200-9996.jpg" "C:\Users\idant\Code\open-swe\open-swe\apps\web\public\nvidia-logo.png"
```

**Option B - Extract/Crop the logo from the image:**
- Open the jpg in an image editor
- Crop just the NVIDIA logo part (eye icon + text)
- Save as: `nvidia-logo.png` (transparent background if possible)
- Copy to: `apps/web/public/nvidia-logo.png`

---

### 2. Enable Image in Component

Once you've added the image, update the logo component to use it:

**File:** `apps/web/src/components/v2/default-view.tsx`

**Change this:**
```tsx
<NVIDIALogo
  height={32}
  showSubtitle={true}
/>
```

**To this:**
```tsx
<NVIDIALogo
  height={32}
  showSubtitle={true}
  useImage={true}  // 👈 Add this!
/>
```

---

### 3. Verify

The logo will automatically load from `/nvidia-logo.png`

If you want to use a different name or format:
1. Place image in `apps/web/public/your-logo-name.png`
2. Update `src/components/icons/nvidia-logo.tsx` line 27:
   ```tsx
   src="/your-logo-name.png"
   ```

---

## 🎨 **Current Setup:**

Right now you'll see:
- ✅ **"NVIDIA"** text with shimmer animation (green)
- ✅ **"NVCRM Agent Swarm"** with shimmer animation (white/gray)
- ✅ **"Powered by NVIDIA NIMs"** subtitle (small, italic)
- ✅ Animated shine effect that sweeps across the text every 4 seconds

---

## 💡 **Recommended Image Specs:**

- **Format:** PNG with transparent background (or SVG)
- **Size:** ~300x80 pixels (will auto-scale)
- **Content:** NVIDIA eye icon + wordmark
- **Background:** Transparent
- **Color:** Full color (the component will use it as-is)

---

**Once you add the image, just set `useImage={true}` and it will replace the placeholder "N" icon!** 🎨



