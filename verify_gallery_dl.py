import gallery_dl.config
import gallery_dl.job

# Set output directory
output_dir = "/tmp/gallery_dl_test"
gallery_dl.config.set(("extractor",), "base-directory", output_dir)

# Verify config is set
print(f"Config base-directory: {gallery_dl.config.get(('extractor',), 'base-directory')}")

# Create a job (won't run it to avoid network calls if not needed, but shows instantiation)
url = "https://example.com/image.jpg"
job = gallery_dl.job.DownloadJob(url)
print(f"Job created for {url}")

# To run it, one would call:
# job.run()
