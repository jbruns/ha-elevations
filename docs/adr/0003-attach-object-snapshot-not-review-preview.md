# Attach the Tracked Object's snapshot, with a new URL on every update

A Notification's image is the moving Tracked Object's cropped snapshot, and every update uses a new URL. We do not attach the Review's preview GIF. The GIF is empty or nearly empty in the first seconds of a Review, which is when the first push goes out. iOS also caches an attachment by URL, so later updates that reused the same URL kept showing the empty image. We accept that the first image may not be the best frame, because it is corrected silently as Frigate improves its snapshot.
