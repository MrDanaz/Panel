// General JavaScript for the panel (if any)
// For now, it can be empty. Specific JS is in templates or will be added here.

document.addEventListener('DOMContentLoaded', function() {
    // Example: Highlight active sidebar link
    const currentPath = window.location.pathname;
    const sidebarLinks = document.querySelectorAll('.sidebar ul li a');

    sidebarLinks.forEach(link => {
        if (link.getAttribute('href') === currentPath) {
            link.classList.add('active');
        }
    });

    // More complex modal logic or dynamic content loading could go here
    // if not handled by inline scripts in templates.
});
