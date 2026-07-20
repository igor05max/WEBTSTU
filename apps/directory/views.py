from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from apps.directory.journal_search import search_journals


@login_required
def journal_search(request):
    query = request.GET.get("q", "")
    journals = search_journals(query, limit=20)
    results = [
        {
            "id": journal.id,
            "name": journal.name,
            "issn": journal.issn,
            "level": journal.white_list_level,
            "label": f"{journal.name} ({journal.issn})" if journal.issn else journal.name,
        }
        for journal in journals
    ]
    return JsonResponse({"results": results})
